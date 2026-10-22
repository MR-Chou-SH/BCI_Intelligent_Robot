using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Net.Sockets;
using BCIIntelligentRobot.Kitchen;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace BCIIntelligentRobot.Kitchen.Editor
{
    public static class KitchenPlatformBuild
    {
        public const string ScenePath="Assets/Scenes/KitchenPlatform.unity";
        public const string QuestScenePath="Assets/Scenes/KitchenPlatformQuest.unity";
        const string ResourceRoot="KitchenPlatformV3/ModelCandidate005";
        static bool batchError;
        static void RecordBatchError(string message,string trace,LogType type)
        {
            if(type==LogType.Error || type==LogType.Exception || type==LogType.Assert)batchError=true;
        }
        public static void CreateAndRenderBatch()
        {
            RunBatch(CreateAndRender);
        }
        public static void BuildQuestApkBatch()
        {
            RunBatch(BuildQuestApk);
        }
        static void RunBatch(Action command)
        {
            batchError=false;Application.logMessageReceived+=RecordBatchError;
            try
            {
                command();
                if(batchError)throw new InvalidOperationException("Editor logged an error during the batch command");
                Debug.Log("KITCHEN_PLATFORM_BATCH_COMPLETE exit=0");
                if(Application.isBatchMode)EditorApplication.Exit(0);
            }
            catch(Exception error)
            {
                Debug.LogException(error);
                if(Application.isBatchMode)EditorApplication.Exit(1);else throw;
            }
            finally { Application.logMessageReceived-=RecordBatchError; }
        }
        static string Output()
        {
            string value=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_OUTPUT");
            if(string.IsNullOrWhiteSpace(value))throw new InvalidOperationException("Explicit KITCHEN_PLATFORM_OUTPUT is required");
            Directory.CreateDirectory(value);return Path.GetFullPath(value);
        }
        static void Light(string name,Vector3 angles,float intensity,Color color)
        {
            var light=new GameObject(name).AddComponent<Light>(); light.type=LightType.Directional;
            light.transform.rotation=Quaternion.Euler(angles);light.intensity=intensity;light.color=color;
            light.shadows=LightShadows.Soft;light.shadowStrength=.55f;
            light.shadowBias=.035f;light.shadowNormalBias=.12f;light.shadowCustomResolution=2048;
        }
        static Camera Camera(string name,Vector3 position,Vector3 target)
        {
            var camera=new GameObject(name).AddComponent<Camera>();camera.transform.position=position;
            camera.transform.LookAt(target);camera.nearClipPlane=.03f;camera.farClipPlane=12;
            camera.fieldOfView=45;camera.backgroundColor=new Color(.43f,.49f,.52f);camera.clearFlags=CameraClearFlags.SolidColor;
            return camera;
        }
        [MenuItem("BCI/Kitchen Platform/Create view-only V3 scene")]
        public static void CreateScene()
        {
            CreateSceneAt(ScenePath,false);
        }
        static void CreateSceneAt(string path,bool quest,bool save=true,string inspectionResourceRoot=null)
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
            var root=new GameObject("MuJoCoKitchenPlatform");
            var mirror=root.AddComponent<KitchenSceneMirror>();mirror.resourceRoot=inspectionResourceRoot??ResourceRoot;
            root.AddComponent<KitchenPlatformArt>().Mirror=mirror;
            var view=new GameObject("ReadOnlyWorldClient").AddComponent<KitchenPlatformView>();view.Mirror=mirror;
            string host=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_HOST");
            view.ServerHost=quest?host:"127.0.0.1";
            string port=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_WORLD_PORT");
            if(!string.IsNullOrWhiteSpace(port))view.WorldPort=int.Parse(port);
            var desktop=Camera("DesktopInspectionCamera",new Vector3(2.15f,2.5f,-2.9f),new Vector3(.05f,.7f,0));
            desktop.tag="MainCamera";
            if(quest)
            {
                var prefab=AssetDatabase.LoadAssetAtPath<GameObject>("Packages/com.meta.xr.sdk.core/Prefabs/OVRCameraRig.prefab");
                if(prefab==null)throw new InvalidOperationException("XR rig asset missing");
                var rig=(GameObject)PrefabUtility.InstantiatePrefab(prefab);rig.name="KitchenPlatformOVRCameraRig";
                rig.transform.position=new Vector3(0,0,-1.8f);
                // Editor desktop camera is explicit. Quest activation is a separate device gate.
            }
            desktop.gameObject.SetActive(!quest);
            RenderSettings.ambientMode=AmbientMode.Trilight;
            RenderSettings.ambientSkyColor=new Color(.68f,.75f,.78f);
            RenderSettings.ambientEquatorColor=new Color(.48f,.51f,.50f);
            RenderSettings.ambientGroundColor=new Color(.25f,.27f,.29f);
            Light("WindowKey",new Vector3(45,-35,0),.95f,new Color(1,.96f,.90f));
            Light("SoftFill",new Vector3(68,135,0),.3f,new Color(.76f,.86f,1));
            QualitySettings.shadows=ShadowQuality.All;QualitySettings.shadowResolution=ShadowResolution.High;
            QualitySettings.antiAliasing=4;
            if(save)
            {
                Directory.CreateDirectory("Assets/Scenes");
                EditorSceneManager.SaveScene(EditorSceneManager.GetActiveScene(),path);
                AssetDatabase.SaveAssets();
            }
            Debug.Log("KITCHEN_PLATFORM_SCENE "+(save?"SAVED":"TRANSIENT")+" viewOnly=true quest=HARDWARE_REQUIRED");
        }
        [MenuItem("BCI/Kitchen Platform/Render V3 inspection views")]
        public static void CreateAndRender()
        {
            string output=Output();
            // Inspection never rewrites the saved scene or its default bindings.
            string inspectionRoot=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_PREVIEW_RESOURCE_ROOT");
            if(!string.IsNullOrEmpty(inspectionRoot) && !System.Text.RegularExpressions.Regex.IsMatch(inspectionRoot,@"^KitchenPlatformV3/ModelCandidate[0-9]{3}$"))
                throw new InvalidDataException("Inspection resource must be an explicit versioned candidate");
            CreateSceneAt(ScenePath,false,false,string.IsNullOrEmpty(inspectionRoot)?null:inspectionRoot);
            var mirror=UnityEngine.Object.FindFirstObjectByType<KitchenSceneMirror>();
            mirror.Build();
            var originalGeoms=new Dictionary<Transform,Matrix4x4>();
            foreach(var geom in mirror.Manifest.geoms)
            {
                var body=Array.Find(mirror.Manifest.bodies,value=>value.id==geom.body_id);
                var source=mirror.transform.Find(body.name+"/"+geom.name);
                if(source==null)throw new InvalidDataException("Exported geom is absent: "+geom.name);
                originalGeoms.Add(source,source.localToWorldMatrix);
            }
            mirror.GetComponent<KitchenPlatformArt>().Apply();
            foreach(var original in originalGeoms)
                for(int axis=0;axis<16;axis++)
                    if(Mathf.Abs(original.Key.localToWorldMatrix[axis]-original.Value[axis])>1e-6f)
                        throw new InvalidDataException("Art changed a native geom transform: "+original.Key.name);
            string expectedLayout=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_LAYOUT");
            string expectedHash=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_SCENE_HASH");
            if(!string.IsNullOrEmpty(expectedLayout) && mirror.Manifest.layout_id!=expectedLayout)
                throw new InvalidDataException("Export/runtime layout mismatch");
            if(!string.IsNullOrEmpty(expectedHash) && mirror.Manifest.model_scene_sha256!=expectedHash)
                throw new InvalidDataException("Export/native model identity mismatch");
            foreach(var filter in mirror.GetComponentsInChildren<MeshFilter>(true))
            {
                if(filter.sharedMesh==null)throw new InvalidDataException("Missing visual mesh: "+filter.name);
                foreach(var vertex in filter.sharedMesh.vertices)
                    if(float.IsNaN(vertex.x) || float.IsNaN(vertex.y) || float.IsNaN(vertex.z) ||
                        float.IsInfinity(vertex.x) || float.IsInfinity(vertex.y) || float.IsInfinity(vertex.z))
                        throw new InvalidDataException("Nonfinite visual mesh: "+filter.sharedMesh.name);
                CheckBounds(filter.sharedMesh.bounds,filter.name);
            }
            if(Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_SYNC_REQUIRED")=="1")ValidateLiveWorld(mirror,output);
            if(mirror.GetComponentsInChildren<Collider>(true).Length!=0 || mirror.GetComponentsInChildren<Rigidbody>(true).Length!=0)
                throw new InvalidOperationException("Unity must have no kitchen contact physics");
            foreach(var renderer in mirror.GetComponentsInChildren<Renderer>(true))CheckBounds(renderer.bounds,renderer.name);
            var camera=GameObject.Find("DesktopInspectionCamera").GetComponent<Camera>();
            Render(camera,output,"overview",new Vector3(2.15f,2.5f,-2.9f),new Vector3(.05f,.7f,0),45);
            Render(camera,output,"room",new Vector3(2.75f,1.9f,-3.25f),new Vector3(.05f,1.05f,.5f),49);
            Render(camera,output,"top",new Vector3(.06f,4.15f,-.23f),new Vector3(.06f,.7f,-.23f),47);
            var sink=mirror.transform.Find("sink");var microwave=mirror.transform.Find("microwave");
            if(sink==null || microwave==null)throw new InvalidDataException("Inspection body is absent");
            Render(camera,output,"sink",sink.TransformPoint(new Vector3(-.65f,.85f,-.95f)),sink.TransformPoint(new Vector3(0,.06f,.08f)),46);
            // The opening faces body-local -X; use its native pose, including the candidate yaw.
            Render(camera,output,"microwave",microwave.TransformPoint(new Vector3(-.82f,.34f,-.15f)),microwave.TransformPoint(new Vector3(-.08f,.17f,0)),43);
            var meal=mirror.transform.Find("burger_container");
            if(meal==null)throw new InvalidDataException("Inspection meal container is absent");
            Render(camera,output,"meal",meal.TransformPoint(new Vector3(.28f,.21f,-.32f)),meal.TransformPoint(new Vector3(0,.035f,0)),43);
            var button=mirror.transform.Find("microwave_button");
            if(button!=null)Render(camera,output,"button",microwave.TransformPoint(new Vector3(-.45f,.24f,-.32f)),button.position,22);
            RenderRecordedFrames(mirror,camera,output);
            RenderComponentFrames(mirror,camera,output);
            string manifestPath="Assets/Resources/"+mirror.resourceRoot+"/scene.json";
            using(var hash=SHA256.Create())
            {
                string identity=BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(manifestPath))).Replace("-","").ToLowerInvariant();
                File.WriteAllText(Path.Combine(output,"editor_render_report.json"),JsonUtility.ToJson(new RenderReport {
                    status="UNITY_EDITOR_RENDERED",scene=ScenePath,resource_root=mirror.resourceRoot,
                    editor_sha256=FileHash("Assets/Editor/KitchenPlatformBuild.cs"),
                    art_sha256=FileHash("Assets/BCI/Kitchen/KitchenPlatformArt.cs"),
                    unity_version=Application.unityVersion,manifest_sha256=identity,layout=mirror.Manifest.layout_id,
                    model_scene_sha256=mirror.Manifest.model_scene_sha256,model_source_sha256=mirror.Manifest.model_source_sha256,
                    model_builder_sha256=mirror.Manifest.model_builder_sha256,model_compiled_sha256=mirror.Manifest.model_compiled_sha256,
                    mesh_bounds="FINITE",geom_transforms="UNCHANGED",geoms_checked=originalGeoms.Count,colliders=0,rigidbodies=0,
                    quality_review="REQUIRES_IMAGE_INSPECTION_NOT_HIGH_QUALITY_PASS",quest="HARDWARE_REQUIRED"},true));
            }
            Debug.Log("KITCHEN_PLATFORM_EDITOR_RENDERED output="+output);
        }
        [Serializable] sealed class RenderReport
        {
            public string status,scene,resource_root,unity_version,manifest_sha256,layout;
            public string editor_sha256,art_sha256;
            public string model_scene_sha256,model_source_sha256,model_builder_sha256,model_compiled_sha256;
            public string mesh_bounds,geom_transforms,quality_review,quest;public int geoms_checked,colliders,rigidbodies;
        }
        static void CheckBounds(Bounds bounds,string name)
        {
            for(int axis=0;axis<3;axis++)
                if(float.IsNaN(bounds.center[axis]) || float.IsInfinity(bounds.center[axis]) ||
                    float.IsNaN(bounds.size[axis]) || float.IsInfinity(bounds.size[axis]) || bounds.size[axis]<0)
                    throw new InvalidDataException("Nonfinite visual mesh bounds: "+name);
        }
        static void ValidateLiveWorld(KitchenSceneMirror mirror,string output)
        {
            string host=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_HOST")??"127.0.0.1";
            int port=int.Parse(Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_WORLD_PORT"));
            int count=5;
            string requested=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_SYNC_FRAMES");
            if(!string.IsNullOrEmpty(requested) && (!int.TryParse(requested,out count) || count<5 || count>250))
                throw new InvalidDataException("Live sync must be bounded to 5..250 frames");
            string identityPath=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_SYNC_IDENTITY_REPORT");
            ComponentSourceReport identity=null;
            if(!string.IsNullOrEmpty(identityPath))
            {
                identity=ReadIdentityReport(identityPath,mirror.Manifest,Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_CONTROL_HASH"));
                if(identity.status!="PLATFORM_MANIFEST_IDENTITY_PASS" || identity.manifest.runtime==null)
                    throw new InvalidDataException("Live sync requires the actual runtime manifest identity");
            }
            using(var client=new TcpClient(host,port))
            using(var reader=new StreamReader(client.GetStream()))
            using(var frames=new StreamWriter(Path.Combine(output,"world_frames.jsonl")))
            {
                client.ReceiveTimeout=3000;long first=-1,last=-1,previousSequence=-1;float worst=0,rotationError=0,motion=0;
                string session=null,epoch=null;
                var initial=new Dictionary<int,Vector3>();
                var camera=GameObject.Find("DesktopInspectionCamera").GetComponent<Camera>();
                File.WriteAllText(Path.Combine(output,"sync_connected.txt"),"ACTUAL_TCP_WORLD_CONNECTED");
                for(int frame=0;frame<count;frame++)
                {
                    string line=reader.ReadLine();if(line==null)throw new EndOfStreamException("World stream closed");
                    frames.WriteLine(line);frames.Flush();
                    var world=JsonUtility.FromJson<KitchenWorld>(line);
                    if(identity!=null && (world.runtimeSession!=identity.manifest.runtime.runtime_session ||
                        world.sceneEpoch!=identity.manifest.runtime.epoch.ToString()))
                        throw new InvalidDataException("World session differs from the actual control-bound runtime");
                    // Stream transmission and physics advancement are independent 40 ms loops.
                    if(frame>0 && (world.simTick<last || world.sequence<=previousSequence ||
                        world.runtimeSession!=session || world.sceneEpoch!=epoch))
                        throw new InvalidDataException("World stream did not advance within one native identity");
                    mirror.Apply(world);
                    previousSequence=world.sequence;session=world.runtimeSession;epoch=world.sceneEpoch;
                    if(first<0)first=world.simTick;last=world.simTick;
                    foreach(var body in world.bodies)
                    {
                        var target=mirror.transform.Find(string.IsNullOrEmpty(body.name)?"body_"+body.id:body.name);
                        if(target==null)throw new InvalidDataException("Mirrored body is absent");
                        worst=Mathf.Max(worst,Vector3.Distance(target.localPosition,KitchenSceneMirror.NativeVector(body.position)));
                        rotationError=Mathf.Max(rotationError,Quaternion.Angle(target.localRotation,KitchenSceneMirror.NativeRotation(body.quaternion_wxyz)));
                        if(rotationError>.01f)throw new InvalidDataException("World rotation mismatch");
                        if(body.name.StartsWith("left_fr3_") || body.name.StartsWith("right_fr3_") || body.name.Contains("_umi_"))
                        {
                            if(frame==0)initial.Add(body.id,target.localPosition);
                            motion=Mathf.Max(motion,Vector3.Distance(initial[body.id],target.localPosition));
                        }
                    }
                    if(count>5 && (frame==0 || frame==count/2 || frame==count-1))
                        Render(camera,output,"live_robot_"+frame.ToString("D3"),new Vector3(2.15f,2.5f,-2.9f),new Vector3(.05f,.7f,0),45);
                    string priorHash=world.sceneHash;world.sceneHash="invalid-scene-hash";bool rejected=false;
                    try{mirror.Apply(world);}catch(InvalidDataException){rejected=true;}
                    if(!rejected || mirror.LastSequence!=world.sequence || mirror.LastTick!=world.simTick)
                        throw new InvalidDataException("Mismatched scene was accepted or mutated mirror state");
                    world.sceneHash=priorHash;
                }
                if(last<=first || worst>1e-5f)throw new InvalidDataException("World clock did not advance or transforms differ");
                if(count>5 && motion<1e-5f)throw new InvalidDataException("No measurable actual robot-body motion was synchronized");
                File.WriteAllText(Path.Combine(output,"world_sync_report.json"),JsonUtility.ToJson(new SyncReport {
                    status="PC_UNITY_WORLD_SYNC_PASS",layout=mirror.Manifest.layout_id,model_scene_sha256=mirror.Manifest.model_scene_sha256,
                    control_hash=identity==null?null:identity.manifest.control_identity.control_hash,
                    identity_report_sha256=identity==null?null:FileHash(identityPath),
                    runtime_session=session,scene_epoch=epoch,frames=count,bodies_per_frame=mirror.Manifest.bodies.Length,
                    first_tick=first,last_tick=last,position_error_m=worst,bad_identity_rejected=true,
                    rotation_error_deg=rotationError,robot_body_motion_m=motion,
                    bad_identity_rejections=count,quest="HARDWARE_REQUIRED"},true));
            }
        }
        [Serializable] sealed class SyncReport
        {
            public string status,layout,model_scene_sha256,runtime_session,scene_epoch,quest,control_hash,identity_report_sha256;
            public int frames,bodies_per_frame,bad_identity_rejections;public long first_tick,last_tick;
            public float position_error_m,rotation_error_deg,robot_body_motion_m;public bool bad_identity_rejected;
        }
        [Serializable] sealed class RecordedPacket
        {
            public string label;public KitchenWorld world;
        }
        [Serializable] sealed class RecordedProvenance
        {
            public string label,source_record,record_sha256,source_scene_hash,display_scene_hash;
            public string compiled_mjb_hash,layout_hash,hash_chain,body_pose_derivation,identity_rebinding;
            public string physical_task_success,source_checkpoint_sha256;
            public long selected_tick;public double angle_deg;
        }
        [Serializable] sealed class ProvenanceList
        {
            public RecordedProvenance[] entries;
        }
        [Serializable] sealed class ReplayEntry
        {
            public string label,screenshot,body,source_record,record_sha256,actual_record_sha256;
            public string source_scene_hash,identity_rebinding,runtime_session,scene_epoch;
            public long tick;public double recorded_angle_deg;
            public int mirrored_bodies;public float position_error_m,rotation_error_deg;
            public float[] recorded_native_position,recorded_native_quaternion_wxyz;
            public Vector3 actual_unity_local_position;public Quaternion actual_unity_local_rotation_xyzw;
        }
        [Serializable] sealed class ReplayReport
        {
            public string status,input,provenance,input_sha256,provenance_sha256,layout,model_scene_sha256;
            public string model_compiled_sha256,layout_manifest_sha256,error;
            public string evidence="OFFLINE_KINEMATIC_REPLAY_OF_RECORDED_MUJOCO_QPOS";
            public string physical_task_success="NOT_INFERRED_FROM_REPLAY",live_transport="NOT_THIS_REPORT";
            public List<ReplayEntry> frames=new List<ReplayEntry>();
        }
        static string FileHash(string path)
        {
            using(var hash=SHA256.Create())using(var stream=File.OpenRead(path))
                return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-","").ToLowerInvariant();
        }
        static void RenderRecordedFrames(KitchenSceneMirror mirror,Camera camera,string output)
        {
            string input=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_REPLAY_FRAMES");
            if(string.IsNullOrWhiteSpace(input))return;
            var report=new ReplayReport {status="FAIL",input=Path.GetFullPath(input),layout=mirror.Manifest.layout_id,
                model_scene_sha256=mirror.Manifest.model_scene_sha256,model_compiled_sha256=mirror.Manifest.model_compiled_sha256,
                layout_manifest_sha256=mirror.Manifest.layout_manifest_sha256};
            try
            {
                report.provenance=Path.Combine(Path.GetDirectoryName(report.input),"recorded_visual_provenance.json");
                // Preserve exact replay inputs separately from the live TCP capture.
                string snapshot=Path.Combine(output,"recorded_visual_frames.jsonl");
                string provenanceSnapshot=Path.Combine(output,"recorded_visual_provenance.json");
                File.Copy(report.input,snapshot,false);File.Copy(report.provenance,provenanceSnapshot,false);
                report.input_sha256=FileHash(snapshot);report.provenance_sha256=FileHash(provenanceSnapshot);
                var sources=JsonUtility.FromJson<ProvenanceList>("{\"entries\":"+File.ReadAllText(provenanceSnapshot)+"}");
                if(sources==null || sources.entries==null || sources.entries.Length!=2)
                    throw new InvalidDataException("Expected two recorded provenance entries");
                var seen=new HashSet<string>();
                foreach(string line in File.ReadAllLines(snapshot))
                {
                    if(string.IsNullOrWhiteSpace(line))continue;
                    var packet=JsonUtility.FromJson<RecordedPacket>(line);
                    if(packet==null || (packet.label!="recorded_door_open" && packet.label!="recorded_faucet_on") ||
                        !seen.Add(packet.label) || packet.world==null)
                        throw new InvalidDataException("Unknown, duplicate or incomplete recorded frame");
                    var source=Array.Find(sources.entries,item=>item!=null && item.label==packet.label);
                    if(source==null || Array.FindAll(sources.entries,item=>item!=null && item.label==packet.label).Length!=1)
                        throw new InvalidDataException("Missing or duplicate provenance for "+packet.label);
                    var entry=new ReplayEntry {label=packet.label,screenshot=packet.label+".png",source_record=source.source_record,
                        record_sha256=source.record_sha256,source_scene_hash=source.source_scene_hash,
                        identity_rebinding=source.identity_rebinding,tick=packet.world.simTick,recorded_angle_deg=source.angle_deg,
                        runtime_session=packet.world.runtimeSession,scene_epoch=packet.world.sceneEpoch};
                    report.frames.Add(entry);
                    entry.actual_record_sha256=FileHash(source.source_record);
                    if(entry.actual_record_sha256!=source.record_sha256)
                        throw new InvalidDataException("Recorded source SHA256 mismatch: actual="+entry.actual_record_sha256+" expected="+source.record_sha256);
                    if(source.hash_chain!="PASS" || source.display_scene_hash!=mirror.Manifest.model_scene_sha256 ||
                        source.compiled_mjb_hash!=mirror.Manifest.model_compiled_sha256 || source.layout_hash!=mirror.Manifest.layout_manifest_sha256 ||
                        source.selected_tick!=packet.world.simTick || source.physical_task_success!="NOT_INFERRED_FROM_REPLAY" ||
                        string.IsNullOrWhiteSpace(source.body_pose_derivation) || string.IsNullOrWhiteSpace(source.identity_rebinding))
                        throw new InvalidDataException("Recorded provenance/native export identity differs");
                    mirror.Apply(packet.world);
                    if(mirror.LastTick!=packet.world.simTick || mirror.LastSequence!=packet.world.sequence ||
                        mirror.LastRuntimeSession!=packet.world.runtimeSession || mirror.LastEpoch!=packet.world.sceneEpoch)
                        throw new InvalidDataException("Recorded frame was not applied");
                    entry.mirrored_bodies=packet.world.bodies.Length;
                    foreach(var body in packet.world.bodies)
                    {
                        var target=mirror.transform.Find(string.IsNullOrEmpty(body.name)?"body_"+body.id:body.name);
                        if(target==null)throw new InvalidDataException("Recorded body is absent");
                        entry.position_error_m=Mathf.Max(entry.position_error_m,Vector3.Distance(target.localPosition,KitchenSceneMirror.NativeVector(body.position)));
                        entry.rotation_error_deg=Mathf.Max(entry.rotation_error_deg,Quaternion.Angle(target.localRotation,KitchenSceneMirror.NativeRotation(body.quaternion_wxyz)));
                    }
                    if(entry.position_error_m>1e-5f || entry.rotation_error_deg>.01f)
                        throw new InvalidDataException("Recorded mirrored transforms differ");
                    entry.body=packet.label=="recorded_door_open"?"microwave_door":"faucet_handle";
                    var affected=mirror.transform.Find(entry.body);
                    var native=Array.Find(packet.world.bodies,item=>item.name==entry.body);
                    if(affected==null || native==null)throw new InvalidDataException("Recorded moving body is absent");
                    entry.recorded_native_position=native.position;entry.recorded_native_quaternion_wxyz=native.quaternion_wxyz;
                    entry.actual_unity_local_position=affected.localPosition;entry.actual_unity_local_rotation_xyzw=affected.localRotation;
                    foreach(var renderer in mirror.GetComponentsInChildren<Renderer>(true))CheckBounds(renderer.bounds,renderer.name);
                    if(packet.label=="recorded_door_open")
                    {
                        var device=mirror.transform.Find("microwave");
                        Render(camera,output,"recorded_door_open",device.TransformPoint(new Vector3(-1.05f,.50f,-.45f)),device.TransformPoint(new Vector3(-.16f,.17f,-.01f)),48);
                    }
                    else
                    {
                        var device=mirror.transform.Find("faucet");
                        if(device==null)throw new InvalidDataException("Recorded faucet base is absent");
                        Render(camera,output,"recorded_faucet_on",device.TransformPoint(new Vector3(-.42f,.52f,-.63f)),device.TransformPoint(new Vector3(.02f,.17f,-.07f)),43);
                    }
                }
                if(seen.Count!=2)throw new InvalidDataException("Both constant recorded labels are required");
                if(FileHash(report.input)!=report.input_sha256 || FileHash(report.provenance)!=report.provenance_sha256)
                    throw new InvalidDataException("Recorded inputs changed during rendering");
                report.status="OFFLINE_RECORDED_KINEMATIC_REPLAY_RENDERED";
            }
            catch(Exception error){report.error=error.Message;throw;}
            finally{File.WriteAllText(Path.Combine(output,"recorded_replay_report.json"),JsonUtility.ToJson(report,true));}
        }
        [Serializable] sealed class ComponentPhase
        {
            public string label,screenshot;public long tick;public double time_s;
            public bool water_on;public float apple_clean_seconds;
        }
        [Serializable] sealed class ComponentProvenance
        {
            public string status,source_report,source_report_sha256,source_record,source_record_sha256;
            public string input_sha256,manifest_sha256,layout,scene_hash,compiled_model_hash,layout_hash,control_hash;
            public string hash_chain,identity_rebinding,faucet_motion,heating_retrieval,source_component_status;
            public int packets,video_frames,video_fps,physics_steps,source_physics_samples;
            public double source_duration_s;public ComponentPhase[] phases;
        }
        [Serializable] sealed class ComponentPacket
        {
            public string label,sample_sha256;public int video_index;public double time_s;
            public float[] actual_ctrl16;public KitchenWorld world;
            public ComponentDevice device_state;
        }
        [Serializable] sealed class ComponentCleanTime { public float apple; }
        [Serializable] sealed class ComponentDevice { public bool water_on;public ComponentCleanTime clean_seconds; }
        [Serializable] sealed class ComponentControl { public string control_hash; }
        [Serializable] sealed class ComponentRuntime
        {
            public string runtime_session,scene_hash,control_hash;public int epoch;
        }
        [Serializable] sealed class ComponentManifest
        {
            public string layout_id,scene_hash,compiled_model_hash,layout_hash;
            public ComponentControl control_identity;
            public ComponentRuntime runtime;
        }
        [Serializable] sealed class ComponentSourceIdentity { public ComponentManifest manifest; }
        [Serializable] sealed class ComponentSourceReport
        {
            public string status;public ComponentManifest manifest;
            public ComponentSourceIdentity identity;
        }
        static ComponentSourceReport ReadIdentityReport(string path,KitchenManifest manifest,string controlHash)
        {
            var report=JsonUtility.FromJson<ComponentSourceReport>(File.ReadAllText(path));
            if(report==null || (report.status!="REST_PHYSICS_PASS" && report.status!="PLATFORM_MANIFEST_IDENTITY_PASS" && report.status!="COMPLETE_MICROWAVE_CYCLE_PASS") ||
                report.manifest==null || report.manifest.layout_id!=manifest.layout_id ||
                report.manifest.scene_hash!=manifest.model_scene_sha256 || report.manifest.compiled_model_hash!=manifest.model_compiled_sha256 ||
                report.manifest.layout_hash!=manifest.layout_manifest_sha256 || string.IsNullOrWhiteSpace(controlHash) ||
                report.manifest.control_identity==null || report.manifest.control_identity.control_hash!=controlHash)
                throw new InvalidDataException("Actual source manifest does not match the candidate/control identity");
            if(report.status=="PLATFORM_MANIFEST_IDENTITY_PASS" && (report.manifest.runtime==null ||
                string.IsNullOrWhiteSpace(report.manifest.runtime.runtime_session) ||
                report.manifest.runtime.scene_hash!=manifest.model_scene_sha256 || report.manifest.runtime.control_hash!=controlHash))
                throw new InvalidDataException("Actual runtime/control token is missing or mismatched");
            return report;
        }
        [Serializable] sealed class ComponentRenderReport
        {
            public string status="FAIL",error,input,provenance_sha256;
            public ComponentProvenance source;
            public int packets_rendered,video_frames_rendered,bodies_per_frame,colliders,rigidbodies;
            public int water_on_packets,water_state_transitions;
            public long first_tick=-1,last_tick=-1;
            public float position_error_m,rotation_error_deg;
            public bool bad_identity_rejected,child_local_transforms_unchanged;
            public string evidence="OFFLINE_SAME_RECORD_KINEMATIC_REPLAY_NOT_LIVE_TRANSPORT";
            public string physical_task_success="NOT_INFERRED_FROM_REPLAY",quest="HARDWARE_REQUIRED";
            public List<ComponentPhase> phases=new List<ComponentPhase>();
        }
        static void RenderComponentFrames(KitchenSceneMirror mirror,Camera camera,string output)
        {
            string input=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_COMPONENT_REPLAY");
            if(string.IsNullOrWhiteSpace(input))return;
            if(!string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_REPLAY_FRAMES")))
                throw new InvalidDataException("Component replay must not mix with legacy recorded frames");
            var report=new ComponentRenderReport {input=Path.GetFullPath(input),bodies_per_frame=mirror.Manifest.bodies.Length};
            try
            {
                string provenance=Path.Combine(Path.GetDirectoryName(report.input),"component_provenance.json");
                report.provenance_sha256=FileHash(provenance);
                var source=JsonUtility.FromJson<ComponentProvenance>(File.ReadAllText(provenance));report.source=source;
                if(source==null || source.status!="SAME_RECORD_KINEMATIC_PACKET_DERIVATION_PASS" || source.hash_chain!="PASS" ||
                    source.physics_steps!=0 || !System.Text.RegularExpressions.Regex.IsMatch(source.layout??"",@"^kitchen_v3_norice_candidate_(011|013)$") ||
                    source.identity_rebinding!="NONE_EXACT_SAME_"+source.layout.Substring(source.layout.Length-3) ||
                    source.layout!=mirror.Manifest.layout_id || source.scene_hash!=mirror.Manifest.model_scene_sha256 ||
                    source.compiled_model_hash!=mirror.Manifest.model_compiled_sha256 || source.layout_hash!=mirror.Manifest.layout_manifest_sha256 ||
                    source.manifest_sha256!=FileHash("Assets/Resources/"+mirror.resourceRoot+"/scene.json") || source.input_sha256!=FileHash(report.input) ||
                    source.source_report_sha256!=FileHash(source.source_report) || source.source_record_sha256!=FileHash(source.source_record))
                    throw new InvalidDataException("Exact component record/resource provenance mismatch");
                var physical=JsonUtility.FromJson<ComponentSourceReport>(File.ReadAllText(source.source_report));
                bool apple=physical!=null && physical.status=="APPLE_FULL_CYCLE_MUJOCO_PASS";
                var physicalManifest=apple?physical.identity?.manifest:physical?.manifest;
                if(physical==null || (physical.status!="CAVITY_PLACEMENT_COMPONENT_PASS" && physical.status!="COMPLETE_MICROWAVE_CYCLE_PASS" && !apple) ||
                    physical.status!=source.source_component_status || physicalManifest==null ||
                    physicalManifest.layout_id!=source.layout || physicalManifest.scene_hash!=source.scene_hash ||
                    physicalManifest.compiled_model_hash!=source.compiled_model_hash || physicalManifest.layout_hash!=source.layout_hash ||
                    physicalManifest.control_identity==null || physicalManifest.control_identity.control_hash!=source.control_hash)
                    throw new InvalidDataException("Source physical report identity/control binding differs");
                if(source.layout.EndsWith("013") && source.control_hash!=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_CONTROL_HASH"))
                    throw new InvalidDataException("Final 013 replay must match the explicitly approved control hash");
                string snapshot=Path.Combine(output,"component_frames.jsonl");
                File.Copy(report.input,snapshot,false);File.Copy(provenance,Path.Combine(output,"component_provenance.json"),false);
                string video=Path.Combine(output,"frames");Directory.CreateDirectory(video);
                var children=new Dictionary<Transform,Matrix4x4>();
                foreach(var body in mirror.Manifest.bodies)
                {
                    var root=mirror.transform.Find(body.name);
                    if(root==null)throw new InvalidDataException("Component body absent: "+body.name);
                    foreach(var child in root.GetComponentsInChildren<Transform>(true))
                        if(child!=root)children.Add(child,Matrix4x4.TRS(child.localPosition,child.localRotation,child.localScale));
                }
                var labels=new HashSet<string>();long sequence=-1;double time=-1;string session=null,epoch=null;bool? water=null;
                foreach(string line in File.ReadLines(snapshot))
                {
                    if(string.IsNullOrWhiteSpace(line))throw new InvalidDataException("Empty component packet");
                    var packet=JsonUtility.FromJson<ComponentPacket>(line);var world=packet?.world;
                    if(world==null || world.simTick<=report.last_tick || world.sequence<=sequence || world.stopped ||
                        double.IsNaN(packet.time_s) || double.IsInfinity(packet.time_s) || packet.time_s<=time ||
                        packet.actual_ctrl16==null || packet.actual_ctrl16.Length!=16 || packet.device_state==null ||
                        packet.sample_sha256==null || packet.sample_sha256.Length!=64 ||
                        (session!=null && (session!=world.runtimeSession || epoch!=world.sceneEpoch)))
                        throw new InvalidDataException("Invalid/nonmonotonic component packet");
                    foreach(float value in packet.actual_ctrl16)
                        if(float.IsNaN(value) || float.IsInfinity(value))throw new InvalidDataException("Nonfinite recorded control");
                    mirror.Apply(world);sequence=world.sequence;time=packet.time_s;session=world.runtimeSession;epoch=world.sceneEpoch;
                    if(packet.device_state.water_on)report.water_on_packets++;
                    if(water.HasValue && water.Value!=packet.device_state.water_on)report.water_state_transitions++;
                    water=packet.device_state.water_on;
                    if(mirror.LastTick!=world.simTick || mirror.LastSequence!=world.sequence)throw new InvalidDataException("Component mirror rejected a valid packet");
                    if(report.first_tick<0)report.first_tick=world.simTick;report.last_tick=world.simTick;report.packets_rendered++;
                    foreach(var body in world.bodies)
                    {
                        var target=mirror.transform.Find(body.name);
                        if(target==null)throw new InvalidDataException("Component body name mismatch");
                        report.position_error_m=Mathf.Max(report.position_error_m,Vector3.Distance(target.localPosition,KitchenSceneMirror.NativeVector(body.position)));
                        report.rotation_error_deg=Mathf.Max(report.rotation_error_deg,Quaternion.Angle(target.localRotation,KitchenSceneMirror.NativeRotation(body.quaternion_wxyz)));
                    }
                    foreach(var child in children)
                    {
                        var matrix=Matrix4x4.TRS(child.Key.localPosition,child.Key.localRotation,child.Key.localScale);
                        for(int axis=0;axis<16;axis++)if(Mathf.Abs(matrix[axis]-child.Value[axis])>1e-6f)
                            throw new InvalidDataException("Component replay changed native/art local geometry");
                    }
                    foreach(var renderer in mirror.GetComponentsInChildren<Renderer>(true))CheckBounds(renderer.bounds,renderer.name);
                    if(packet.video_index>=0)
                    {
                        if(packet.video_index!=report.video_frames_rendered)throw new InvalidDataException("Nonconsecutive rendered frame index");
                        Render(camera,video,"frame_"+packet.video_index.ToString("D5"),new Vector3(2.15f,2.5f,-2.9f),new Vector3(.05f,.7f,0),45,800,550);
                        report.video_frames_rendered++;
                    }
                    if(!string.IsNullOrEmpty(packet.label))
                    {
                        var phase=Array.Find(source.phases,item=>item.label==packet.label);
                        if(phase==null || !labels.Add(packet.label) || phase.tick!=world.simTick || Math.Abs(phase.time_s-packet.time_s)>1e-6)
                            throw new InvalidDataException("Component phase differs from its source tick");
                        string image="component_"+packet.label;
                        var device=mirror.transform.Find(apple?(packet.label.StartsWith("tray",StringComparison.Ordinal)?"wood_tray":"sink"):"microwave");
                        if(apple)
                            Render(camera,output,image,device.TransformPoint(new Vector3(-.65f,.85f,-.95f)),device.TransformPoint(new Vector3(0,.06f,.08f)),46);
                        else
                            Render(camera,output,image,device.TransformPoint(new Vector3(-1.05f,.50f,-.45f)),device.TransformPoint(new Vector3(-.16f,.17f,-.01f)),48);
                        report.phases.Add(new ComponentPhase {label=packet.label,tick=world.simTick,time_s=packet.time_s,screenshot=image+".png",
                            water_on=packet.device_state.water_on,apple_clean_seconds=packet.device_state.clean_seconds?.apple??0});
                        if(apple && (packet.label=="faucet_on" || packet.label=="apple_wash_state_complete" || packet.label=="faucet_off_released"))
                        {
                            var faucet=mirror.transform.Find("faucet");
                            Render(camera,output,"detail_"+packet.label,faucet.TransformPoint(new Vector3(-.42f,.52f,-.63f)),faucet.TransformPoint(new Vector3(.02f,.17f,-.07f)),43);
                        }
                        if(packet.label=="button_force_preload" || packet.label=="physical_heat_started")
                            Render(camera,output,"detail_"+packet.label,device.TransformPoint(new Vector3(-.45f,.24f,-.32f)),mirror.transform.Find("microwave_button").position,35);
                    }
                    if(!report.bad_identity_rejected)
                    {
                        string valid=world.sceneHash;world.sceneHash="invalid-component-scene";
                        try{mirror.Apply(world);}catch(InvalidDataException){report.bad_identity_rejected=true;}
                        world.sceneHash=valid;
                        if(!report.bad_identity_rejected || mirror.LastTick!=world.simTick || mirror.LastSequence!=world.sequence)
                            throw new InvalidDataException("Bad identity was accepted or mutated component mirror state");
                    }
                }
                report.colliders=mirror.GetComponentsInChildren<Collider>(true).Length;
                report.rigidbodies=mirror.GetComponentsInChildren<Rigidbody>(true).Length;
                if(report.packets_rendered!=source.packets || report.video_frames_rendered!=source.video_frames ||
                    report.phases.Count!=source.phases.Length || report.position_error_m>1e-5f || report.rotation_error_deg>.01f ||
                    report.colliders!=0 || report.rigidbodies!=0 || source.input_sha256!=FileHash(report.input) ||
                    report.provenance_sha256!=FileHash(provenance) || source.source_report_sha256!=FileHash(source.source_report) ||
                    source.source_record_sha256!=FileHash(source.source_record))
                    throw new InvalidDataException("Component replay completeness/geometry/preservation check failed");
                report.child_local_transforms_unchanged=true;
                if(apple && (report.water_on_packets==0 || report.water_state_transitions!=2))
                    throw new InvalidDataException("Actual apple water-on/off record presentation is incomplete");
                report.status="UNITY_EXACT_SAME_RECORD_REPLAY_RENDERED_NOT_LIVE_NOT_HARDWARE";
            }
            catch(Exception error){report.error=error.Message;throw;}
            finally{File.WriteAllText(Path.Combine(output,"component_replay_report.json"),JsonUtility.ToJson(report,true));}
        }
        static void Render(Camera camera,string directory,string name,Vector3 position,Vector3 target,float fov,int width=1600,int height=1100)
        {
            camera.transform.position=position;
            Vector3 direction=(target-position).normalized;
            camera.transform.LookAt(target,Mathf.Abs(Vector3.Dot(direction,Vector3.up))>.98f?Vector3.forward:Vector3.up);
            camera.fieldOfView=fov;
            var targetTexture=new RenderTexture(width,height,24){antiAliasing=4};
            var pixels=new Texture2D(width,height,TextureFormat.RGB24,false);
            try
            {
                camera.targetTexture=targetTexture;camera.Render();RenderTexture.active=targetTexture;
                pixels.ReadPixels(new Rect(0,0,width,height),0,0);pixels.Apply();
                File.WriteAllBytes(Path.Combine(directory,name+".png"),pixels.EncodeToPNG());
            }
            finally{camera.targetTexture=null;RenderTexture.active=null;targetTexture.Release();UnityEngine.Object.DestroyImmediate(targetTexture);UnityEngine.Object.DestroyImmediate(pixels);}
        }
        [Serializable] sealed class ApkReport
        {
            public string result="FAILED",error,host,layout,resource_root,manifest_sha256,model_scene_sha256;
            public string model_source_sha256,model_builder_sha256,model_compiled_sha256,layout_manifest_sha256;
            public string editor_sha256,art_sha256,mirror_sha256,view_sha256,apk_sha256,unity_version;
            public string quest_scene_sha256,scene_source,build_scene,build_scene_sha256;
            public string control_hash,build_identity_sha256,control_identity_source,identity_evidence;
            public string build_mode,gradle_export_path,application_identifier,product_name;
            public bool temporary_build_settings_restored;
            public string quest="HARDWARE_REQUIRED",deployment="NOT_ATTEMPTED",physics_authority="MuJoCo only";
            public int world_port,errors,warnings;public ulong bytes;public double duration_seconds;
        }
        public static void BuildQuestApk()
        {
            string host=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_HOST");
            if(string.IsNullOrWhiteSpace(host) || host=="localhost" || host=="0.0.0.0" ||
                (System.Net.IPAddress.TryParse(host,out var address) && System.Net.IPAddress.IsLoopback(address)))
                throw new InvalidOperationException("APK requires explicit reachable PC LAN KITCHEN_PLATFORM_HOST; loopback is not a Quest endpoint");
            string output=Output();
            string resource=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_BUILD_RESOURCE_ROOT");
            bool explicitResource=!string.IsNullOrWhiteSpace(resource);
            if(explicitResource && !System.Text.RegularExpressions.Regex.IsMatch(resource,@"^KitchenPlatformV3/ModelCandidate[0-9]{3}$"))
                throw new InvalidDataException("Build resource must be an explicit versioned candidate");
            if(!explicitResource)resource=ResourceRoot;
            var evidence=new ApkReport {host=host,resource_root=resource,unity_version=Application.unityVersion};
            try
            {
                string manifestPath="Assets/Resources/"+resource+"/scene.json";
                var manifest=JsonUtility.FromJson<KitchenManifest>(File.ReadAllText(manifestPath));
                if(manifest==null || manifest.layout_id!=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_LAYOUT") ||
                    manifest.model_scene_sha256!=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_SCENE_HASH"))
                    throw new InvalidDataException("APK requires the verified native/export layout and scene identity");
                evidence.world_port=int.Parse(Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_WORLD_PORT"));
                if(evidence.world_port<1 || evidence.world_port>65535)throw new InvalidDataException("Invalid APK world port");
                evidence.layout=manifest.layout_id;evidence.manifest_sha256=FileHash(manifestPath);
                evidence.model_scene_sha256=manifest.model_scene_sha256;evidence.model_source_sha256=manifest.model_source_sha256;
                evidence.model_builder_sha256=manifest.model_builder_sha256;evidence.model_compiled_sha256=manifest.model_compiled_sha256;
                evidence.layout_manifest_sha256=manifest.layout_manifest_sha256;
                evidence.editor_sha256=FileHash("Assets/Editor/KitchenPlatformBuild.cs");
                evidence.art_sha256=FileHash("Assets/BCI/Kitchen/KitchenPlatformArt.cs");
                evidence.mirror_sha256=FileHash("Assets/BCI/Kitchen/KitchenSceneMirror.cs");
                evidence.view_sha256=FileHash("Assets/BCI/Kitchen/KitchenPlatformView.cs");
                evidence.build_scene=QuestScenePath;
                if(explicitResource)
                {
                    if(Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_REUSE_SAVED_SCENE")=="1")
                        throw new InvalidDataException("An explicit candidate build cannot reuse the old default scene");
                    string identityPath="Assets/Resources/"+resource+"/build_identity.json";
                    var identity=JsonUtility.FromJson<BuildIdentity>(File.ReadAllText(identityPath));
                    if(identity==null || identity.schema!=1 || identity.layout_id!=manifest.layout_id ||
                        identity.scene_hash!=manifest.model_scene_sha256 || identity.compiled_model_hash!=manifest.model_compiled_sha256 ||
                        identity.layout_hash!=manifest.layout_manifest_sha256 || string.IsNullOrWhiteSpace(identity.control_hash) ||
                        identity.control_hash!=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_CONTROL_HASH") ||
                        identity.source_report_sha256!=FileHash(identity.source_report))
                        throw new InvalidDataException("Explicit build/control identity differs from the selected candidate");
                    var physical=ReadIdentityReport(identity.source_report,manifest,identity.control_hash);
                    evidence.identity_evidence=physical.status+"_NOT_A_FULL_OPERATIONS_OR_HARDWARE_PASS";
                    evidence.control_hash=identity.control_hash;evidence.control_identity_source=identity.source_report;
                    evidence.build_identity_sha256=FileHash(identityPath);evidence.quest_scene_sha256=FileHash(QuestScenePath);
                    evidence.build_scene="Assets/BCI/Kitchen/BuildScenes/"+Path.GetFileName(resource)+"Quest.unity";
                    if(File.Exists(evidence.build_scene))throw new InvalidDataException("Never overwrite an existing versioned build scene");
                    Directory.CreateDirectory(Path.GetDirectoryName(evidence.build_scene));
                    CreateSceneAt(evidence.build_scene,true,true,resource);
                    var mirror=UnityEngine.Object.FindFirstObjectByType<KitchenSceneMirror>();
                    var view=UnityEngine.Object.FindFirstObjectByType<KitchenPlatformView>();
                    if(mirror==null || mirror.resourceRoot!=resource || view==null || view.Mirror!=mirror || view.ServerHost!=host || view.WorldPort!=evidence.world_port)
                        throw new InvalidDataException("Versioned build scene bindings differ");
                    evidence.scene_source="NEW_OWNED_VERSIONED_SCENE_EXPLICIT_RESOURCE";
                }
                else if(Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_REUSE_SAVED_SCENE")=="1")
                {
                    evidence.quest_scene_sha256=FileHash(QuestScenePath);
                    EditorSceneManager.OpenScene(QuestScenePath,OpenSceneMode.Single);
                    var mirror=UnityEngine.Object.FindFirstObjectByType<KitchenSceneMirror>();
                    var view=UnityEngine.Object.FindFirstObjectByType<KitchenPlatformView>();
                    if(mirror==null || mirror.resourceRoot!=resource || view==null || view.Mirror!=mirror ||
                        view.ServerHost!=host || view.WorldPort!=evidence.world_port)
                        throw new InvalidDataException("Saved Quest scene bindings differ from the explicit diagnostic build");
                    evidence.scene_source="EXISTING_SAVED_SCENE_UNCHANGED";
                }
                else
                {
                    CreateSceneAt(ScenePath,false);CreateSceneAt(QuestScenePath,true);
                    evidence.scene_source="GENERATED_FROM_VERIFIED_MANIFEST";
                }
                evidence.build_scene_sha256=FileHash(evidence.build_scene);
                string export=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_GRADLE_EXPORT");
                bool exporting=!string.IsNullOrWhiteSpace(export);
                string package=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_ANDROID_PACKAGE");
                string label=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_ANDROID_LABEL");
                if(exporting)
                {
                    if(!Path.IsPathRooted(export) || Path.GetFullPath(export).Length>80 || Directory.Exists(export) || File.Exists(export))
                        throw new InvalidDataException("Gradle export requires a new short absolute directory (at most 80 characters)");
                    if(evidence.scene_source!="EXISTING_SAVED_SCENE_UNCHANGED" && evidence.scene_source!="NEW_OWNED_VERSIONED_SCENE_EXPLICIT_RESOURCE")
                        throw new InvalidDataException("Gradle export must use a verified saved scene or a new explicit versioned scene");
                    export=Path.GetFullPath(export);
                }
                if((exporting || !string.IsNullOrEmpty(package) || !string.IsNullOrEmpty(label)) &&
                    (string.IsNullOrWhiteSpace(label) || string.IsNullOrWhiteSpace(package) ||
                     !System.Text.RegularExpressions.Regex.IsMatch(package,@"^org\.bci\.kitchenplatform\.[a-z0-9]+(?:\.[a-z0-9]+)*$")))
                    throw new InvalidDataException("Explicit dedicated Kitchen package and label are required together");
                evidence.build_mode=exporting?"UNITY_OFFICIAL_GRADLE_EXPORT":"UNITY_DIRECT_APK";
                evidence.gradle_export_path=exporting?export:null;
                var options=new BuildPlayerOptions{scenes=new[]{evidence.build_scene},locationPathName=exporting?export:Path.Combine(output,"KitchenPlatform.apk"),target=BuildTarget.Android,options=BuildOptions.StrictMode|BuildOptions.CompressWithLz4HC};
                if(exporting)options.options|=BuildOptions.AcceptExternalModificationsToPlayer;
                bool prior=PlayerSettings.Android.forceInternetPermission;
                bool priorExport=EditorUserBuildSettings.exportAsGoogleAndroidProject;
                bool priorBundle=EditorUserBuildSettings.buildAppBundle;
                string priorProduct=PlayerSettings.productName;
                string priorPackage=PlayerSettings.GetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Android);
                BuildReport report;
                try
                {
                    PlayerSettings.Android.forceInternetPermission=true;
                    EditorUserBuildSettings.exportAsGoogleAndroidProject=exporting;
                    EditorUserBuildSettings.buildAppBundle=false;
                    if(!string.IsNullOrEmpty(package))
                    {
                        PlayerSettings.SetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Android,package);
                        PlayerSettings.productName=label;
                    }
                    evidence.application_identifier=PlayerSettings.GetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Android);
                    evidence.product_name=PlayerSettings.productName;
                    Debug.Log("KITCHEN_PLATFORM_BUILD mode="+evidence.build_mode+" target="+options.locationPathName);
                    report=BuildPipeline.BuildPlayer(options);
                }
                finally
                {
                    PlayerSettings.Android.forceInternetPermission=prior;
                    PlayerSettings.productName=priorProduct;
                    PlayerSettings.SetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Android,priorPackage);
                    EditorUserBuildSettings.exportAsGoogleAndroidProject=priorExport;
                    EditorUserBuildSettings.buildAppBundle=priorBundle;
                    evidence.temporary_build_settings_restored=PlayerSettings.Android.forceInternetPermission==prior &&
                        PlayerSettings.productName==priorProduct &&
                        PlayerSettings.GetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Android)==priorPackage &&
                        EditorUserBuildSettings.exportAsGoogleAndroidProject==priorExport &&
                        EditorUserBuildSettings.buildAppBundle==priorBundle;
                    if(!evidence.temporary_build_settings_restored)
                        throw new InvalidOperationException("Temporary Android build settings were not restored");
                }
                evidence.result=report.summary.result.ToString();evidence.errors=report.summary.totalErrors;
                evidence.warnings=report.summary.totalWarnings;evidence.bytes=report.summary.totalSize;
                evidence.duration_seconds=report.summary.totalTime.TotalSeconds;
                if(report.summary.result!=BuildResult.Succeeded)throw new InvalidOperationException("APK build failed");
                if(FileHash(manifestPath)!=evidence.manifest_sha256)throw new InvalidDataException("APK export identity changed during build");
                if(FileHash(evidence.build_scene)!=evidence.build_scene_sha256)throw new InvalidDataException("Selected build scene changed during build");
                if(evidence.quest_scene_sha256!=null && FileHash(QuestScenePath)!=evidence.quest_scene_sha256)
                    throw new InvalidDataException("Diagnostic build modified the saved Quest scene");
                if(exporting)
                {
                    foreach(string file in new[]{"settings.gradle","launcher/build.gradle","unityLibrary/build.gradle"})
                        if(!File.Exists(Path.Combine(export,file)))throw new InvalidDataException("Exported Gradle entry missing: "+file);
                    if(!File.Exists(Path.Combine(output,"gradle_callback_report.json")))
                        throw new InvalidDataException("Actual short-path postgenerate callback evidence missing");
                }
                else evidence.apk_sha256=FileHash(options.locationPathName);
            }
            catch(Exception error){evidence.result="FAILED";evidence.error=error.Message;throw;}
            finally{File.WriteAllText(Path.Combine(output,"apk_report.json"),JsonUtility.ToJson(evidence,true));}
        }
        [Serializable] sealed class BuildIdentity
        {
            public int schema;public string layout_id,scene_hash,compiled_model_hash,layout_hash,control_hash;
            public string source_report,source_report_sha256;
        }
    }

    public sealed class KitchenGradleExportPathProbe : UnityEditor.Android.IPostGenerateGradleAndroidProject
    {
        public int callbackOrder => int.MaxValue;
        [Serializable] sealed class CallbackReport
        {
            public string status,callback_path,export_root,package,label;
            public int manifests_checked,longest_manifest_path;
        }
        public void OnPostGenerateGradleAndroidProject(string path)
        {
            string export=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_GRADLE_EXPORT");
            if(string.IsNullOrWhiteSpace(export))return;
            string actual=Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar);
            string expected=Path.Combine(Path.GetFullPath(export),"unityLibrary");
            if(!string.Equals(actual,expected,StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Unity postgenerate callback did not use the requested export: "+actual);
            int longest=0;
            string[] manifests=Directory.GetFiles(Path.GetFullPath(export),"AndroidManifest.xml",SearchOption.AllDirectories);
            foreach(string manifest in manifests)
            {
                longest=Math.Max(longest,manifest.Length);
                var xml=new System.Xml.XmlDocument();xml.Load(manifest);
                if(xml.DocumentElement==null || xml.DocumentElement.Name!="manifest")
                    throw new InvalidDataException("Invalid exported Android manifest: "+manifest);
            }
            if(manifests.Length==0)throw new InvalidDataException("No exported Android manifests");
            string output=Environment.GetEnvironmentVariable("KITCHEN_PLATFORM_OUTPUT");
            File.WriteAllText(Path.Combine(output,"gradle_callback_report.json"),JsonUtility.ToJson(new CallbackReport {
                status="UNITY_ACTUAL_SHORT_EXPORT_CALLBACK_PASS",callback_path=actual,export_root=Path.GetFullPath(export),
                package=PlayerSettings.GetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Android),label=PlayerSettings.productName,
                manifests_checked=manifests.Length,longest_manifest_path=longest},true));
            Debug.Log("KITCHEN_PLATFORM_GRADLE_CALLBACK path="+actual+" manifests="+manifests.Length+" longest="+longest);
        }
    }
}
