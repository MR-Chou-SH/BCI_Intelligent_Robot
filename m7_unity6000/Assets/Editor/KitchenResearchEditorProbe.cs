using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Sockets;
using BCIIntelligentRobot.Kitchen;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace BCIIntelligentRobot.Kitchen.Editor
{
    public static class KitchenResearchEditorProbe
    {
        const string Key="KitchenResearch.Probe";
        static int step;static double deadline;
        static KitchenResearchControls controls;
        static string priorId,seed0Hash,seed0Qpos;
        static readonly List<string> checks=new List<string>();
        static readonly List<JObject> observations=new List<JObject>();
        public static void VerifyResearchInteractive()
        {
            KitchenDemoResearchBuild.CreateResearchScene(false,KitchenDemoResearchBuild.ResearchPC);
            SessionState.SetBool(Key,true);SessionState.SetString(Key+"Output",KitchenDemoResearchBuild.Output());
            Begin();EditorApplication.isPlaying=true;
        }
        [InitializeOnLoadMethod] static void Resume()
        {
            EditorApplication.update-=Probe;
            if(SessionState.GetBool(Key,false))Begin();
        }
        static void Begin(){step=0;deadline=EditorApplication.timeSinceStartup+100;EditorApplication.update-=Probe;EditorApplication.update+=Probe;}
        static void Check(bool value,string name)
        {
            if(!value)throw new InvalidOperationException("Research Editor regression: "+name);
            checks.Add(name);Debug.Log("KITCHEN_RESEARCH_CHECK "+name);
        }
        static void Click(Button button)
        {
            Check(button.interactable,"reset_button_ready");
            ExecuteEvents.Execute(button.gameObject,new PointerEventData(EventSystem.current){button=PointerEventData.InputButton.Left},ExecuteEvents.pointerClickHandler);
        }
        static JObject ObserveAndCompare()
        {
            using(var client=new TcpClient())
            {
                var pending=client.ConnectAsync(controls.ServerHost,controls.PlannerPort);
                if(!pending.Wait(3000))throw new TimeoutException("Research readback connection");pending.GetAwaiter().GetResult();
                client.ReceiveTimeout=5000;client.SendTimeout=3000;
                using(var reader=new StreamReader(client.GetStream()))
                using(var writer=new StreamWriter(client.GetStream()))
                {
                    writer.WriteLine("{\"method\":\"observe\",\"args\":{}}");writer.Flush();
                    var reply=JObject.Parse(reader.ReadLine());Check((bool?)reply["ok"]==true,"independent_pc_observation");
                    var episode=reply["result"]["episode"];var token=reply["token"];
                    Check((string)episode["episode_id"]==controls.EpisodeId &&
                        (string)episode["initialization_hash"]==controls.InitializationHash &&
                        (string)token["runtime_session"]==controls.Mirror.LastRuntimeSession &&
                        token["epoch"].ToString()==controls.Mirror.LastEpoch,"ui_and_world_match_pc_episode");
                    writer.WriteLine("{\"method\":\"get_world_state\",\"args\":{}}");writer.Flush();
                    var worldReply=JObject.Parse(reader.ReadLine());Check((bool?)worldReply["ok"]==true,"independent_pc_world_readback");
                    var world=worldReply["result"];Check((string)world["episode"]["episode_id"]==controls.EpisodeId,"world_same_episode");
                    int compared=0;
                    foreach(var body in world["bodies"])
                    {
                        var shown=controls.Mirror.transform.Find((string)body["name"]);
                        if(shown==null)throw new InvalidDataException("Missing mirrored body "+body["name"]);
                        var position=KitchenSceneMirror.NativeVector(body["position"].ToObject<float[]>());
                        var rotation=KitchenSceneMirror.NativeRotation(body["quaternion_wxyz"].ToObject<float[]>());
                        if(Vector3.Distance(shown.localPosition,position)>1e-6f || Mathf.Abs(Quaternion.Dot(shown.localRotation,rotation))<1-1e-6f)
                            throw new InvalidDataException("PC/Unity body pose mismatch "+body["name"]);
                        compared++;
                    }
                    Check(compared==controls.Mirror.Manifest.bodies.Length,"all_native_body_poses_match_actual_unity_transforms");
                    reply["independent_world_readback"]=worldReply;
                    observations.Add(reply);
                    File.WriteAllText(Path.Combine(SessionState.GetString(Key+"Output",""),"pc_observations.json"),new JArray(observations).ToString(Formatting.Indented));
                    return reply["result"] as JObject;
                }
            }
        }
        static void Capture(string name)=>KitchenDemoResearchBuild.Screenshot(controls.DesktopCamera,
            Path.Combine(SessionState.GetString(Key+"Output",""),name));
        static void Next(){step++;deadline=EditorApplication.timeSinceStartup+75;}
        static void Probe()
        {
            if(!EditorApplication.isPlaying)return;
            try
            {
                if(EditorApplication.timeSinceStartup>deadline)throw new TimeoutException("Research step "+step+": "+controls?.ConnectionState+" "+controls?.Failure);
                if(controls==null)controls=UnityEngine.Object.FindFirstObjectByType<KitchenResearchControls>();
                if(controls==null || controls.ConnectionState!="STREAMING" || controls.NewSeedButton==null || !controls.NewSeedButton.interactable)return;
                if(step==0)
                {
                    Check(UnityEngine.Object.FindObjectsByType<KitchenDemoPlayback>(FindObjectsSortMode.None).Length==0 &&
                        UnityEngine.Object.FindObjectsByType<Rigidbody>(FindObjectsSortMode.None).Length==0,"live_only_no_replay_or_unity_physics");
                    VerifyNearPanel();
                    var observed=ObserveAndCompare();Check(controls.Seed==0,"initial_seed0_passed_through");
                    Check(observed["execution"].Type==JTokenType.Null && controls.ExecutionStatus=="IDLE" &&
                        controls.ExecutionReason=="" && !controls.SafetyStopped,"actual_idle_null_execution_is_displayed_as_idle");
                    seed0Hash=controls.InitializationHash;seed0Qpos=observed["qpos"].ToString(Formatting.None);priorId=controls.EpisodeId;
                    Capture("01_research_seed0.png");controls.SeedInput.text="1";Click(controls.ExplicitSeedButton);Next();
                }
                else if(step==1 && controls.SuccessfulResets==1)
                {
                    var observed=ObserveAndCompare();Check(controls.EpisodeId!=priorId && controls.Seed==1 &&
                        controls.InitializationHash!=seed0Hash && observed["qpos"].ToString(Formatting.None)!=seed0Qpos,"ui_reset_seed1_changed_pc_initialization");
                    Capture("02_research_seed1.png");priorId=controls.EpisodeId;controls.SeedInput.text="0";Click(controls.ExplicitSeedButton);Next();
                }
                else if(step==2 && controls.SuccessfulResets==2)
                {
                    var observed=ObserveAndCompare();Check(controls.EpisodeId!=priorId && controls.Seed==0 &&
                        controls.InitializationHash==seed0Hash && observed["qpos"].ToString(Formatting.None)==seed0Qpos,"ui_repeat_seed0_exact_pc_qpos_new_episode");
                    Capture("03_research_seed0_repeated.png");priorId=controls.EpisodeId;Click(controls.NewSeedButton);Next();
                }
                else if(step==3 && controls.SuccessfulResets==3)
                {
                    ObserveAndCompare();Check(controls.EpisodeId!=priorId && controls.Seed!=0,"ui_new_seed_without_app_restart");
                    Capture("04_research_new_seed.png");priorId=controls.EpisodeId;
                    controls.SeedInput.text="-1";Click(controls.ExplicitSeedButton);
                    Check(controls.Failure.StartsWith("INVALID SEED",StringComparison.Ordinal) && controls.EpisodeId==priorId && controls.SuccessfulResets==3,
                        "invalid_ui_seed_retains_active_episode");
                    var fixture=new JObject { ["stopped"]=true,["stop_reason"]="SYNTHETIC_PRESENTATION_ONLY",
                        ["execution"]=new JObject { ["status"]="ABORTED",["reason"]="SYNTHETIC_PRESENTATION_ONLY" } };
                    var flags=System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic;
                    var apply=typeof(KitchenResearchControls).GetMethod("ApplyOperationState",flags);
                    var labels=typeof(KitchenResearchControls).GetMethod("UpdateLabels",flags);
                    apply.Invoke(controls,new object[]{fixture});labels.Invoke(controls,null);
                    bool shown=false;foreach(var text in UnityEngine.Object.FindObjectsByType<Text>(FindObjectsSortMode.None))if(text.text=="SAFETY STOP")shown=true;
                    Check(controls.SafetyStopped && controls.SafetyStopReason=="SYNTHETIC_PRESENTATION_ONLY" && shown,"synthetic_presentation_only_safety_stop_and_reason");
                    File.WriteAllText(Path.Combine(SessionState.GetString(Key+"Output",""),"safety_feedback_fixture.json"),fixture.ToString(Formatting.Indented));
                    apply.Invoke(controls,new object[]{observations[observations.Count-1]["result"]});labels.Invoke(controls,null);
                    Check(!controls.SafetyStopped,"actual_pc_feedback_restored_after_synthetic_fixture");
                    Finish(null);
                }
            }
            catch(Exception error){Debug.LogException(error);Finish(error);}
        }
        static void VerifyNearPanel()
        {
            var rigObject=new GameObject("ResearchRigProbe");var panelObject=new GameObject("ResearchNearProbe");
            var cameraObject=new GameObject("ResearchNearCamera");var camera=cameraObject.AddComponent<Camera>();
            camera.transform.SetPositionAndRotation(KitchenVrPanel.SeatedEye,Quaternion.identity);camera.fieldOfView=95;camera.nearClipPlane=.03f;
            try
            {
                var rig=rigObject.AddComponent<OVRCameraRig>();rig.centerEyeAnchor.SetPositionAndRotation(camera.transform.position,Quaternion.identity);
                var ui=panelObject.AddComponent<KitchenResearchControls>();ui.enabled=false;ui.Quest=true;
                typeof(KitchenResearchControls).GetMethod("BuildUI",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).Invoke(ui,null);
                ui.Canvas.worldCamera=camera;
                Check(ui.VrPanel.Expanded && Vector3.Distance(ui.Canvas.transform.position,camera.transform.position)>=1 &&
                    Vector3.Distance(ui.Canvas.transform.position,camera.transform.position)<=1.3f,"research_near_world_panel_distance");
                Check(ui.NewSeedButton!=null && ui.ExplicitSeedButton!=null && ui.SeedInput!=null &&
                    panelObject.GetComponent<KitchenDemoPlayback>()==null,"research_controls_independent_from_demo_replay");
                string path=SessionState.GetString(Key+"Output","");
                KitchenDemoResearchBuild.Screenshot(camera,Path.Combine(path,"00_research_near_expanded.png"));
                Canvas.ForceUpdateCanvases();var rect=ui.ExplicitSeedButton.GetComponent<RectTransform>();
                var pointer=new PointerEventData(EventSystem.current){position=RectTransformUtility.WorldToScreenPoint(camera,rect.TransformPoint(rect.rect.center))};
                var hits=new List<RaycastResult>();ui.Canvas.GetComponent<GraphicRaycaster>().Raycast(pointer,hits);
                Check(hits.Exists(hit=>hit.gameObject==ui.ExplicitSeedButton.gameObject),"research_near_actual_reset_button_raycast");
                Vector3 tracked=rig.centerEyeAnchor.localPosition;ui.VrPanel.SetExpanded(false);
                Check(!ui.Canvas.transform.Find("ResearchConsole").gameObject.activeSelf,"research_panel_collapses");
                KitchenDemoResearchBuild.Screenshot(camera,Path.Combine(path,"00_research_near_compact.png"));
                ui.VrPanel.Recall();Check(ui.VrPanel.Expanded && rig.centerEyeAnchor.localPosition==tracked,"research_recall_preserves_head_tracking");
            }
            finally
            {
                if(panelObject.GetComponent<KitchenResearchControls>()?.Canvas!=null)UnityEngine.Object.DestroyImmediate(panelObject.GetComponent<KitchenResearchControls>().Canvas.gameObject);
                UnityEngine.Object.DestroyImmediate(panelObject);UnityEngine.Object.DestroyImmediate(rigObject);UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }
        static void Finish(Exception error)
        {
            var report=new JObject { ["status"]=error==null?"PC_RESEARCH_EDITOR_INTERACTION_PASS":"FAIL",
                ["error"]=error?.ToString(),["checks"]=new JArray(checks),["evidence"]="REAL_EDITOR_UI_AND_INDEPENDENT_PUBLIC_PC_OBSERVE",
                ["successful_ui_resets"]=controls?.SuccessfulResets,["applied_world_frames"]=controls?.View.AppliedFrames,
                ["quest"]="HARDWARE_REQUIRED",["randomized_manipulation"]="NOT_TESTED",["unity"]=Application.unityVersion };
            report["safety_feedback_fixture"]="SYNTHETIC_PRESENTATION_ONLY_NOT_PHYSICS_STOP_EVIDENCE";
            File.WriteAllText(Path.Combine(SessionState.GetString(Key+"Output",""),"editor_interaction_report.json"),report.ToString(Formatting.Indented));
            SessionState.SetBool(Key,false);EditorApplication.update-=Probe;EditorApplication.Exit(error==null?0:1);
        }
    }
}
