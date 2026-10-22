using System;
using System.Collections.Generic;
using System.IO;
using BCIIntelligentRobot.Kitchen;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace BCIIntelligentRobot.Kitchen.Editor
{
    // One real-time playback of both audited episodes, without generating physics or bulk frames.
    public static class KitchenDemoContinuousProbe
    {
        const string Key="KitchenDemo.ContinuousProbe";
        static KitchenDemoPlayback player;
        static DemoPoseFrame[] source;
        static Transform[] shown;
        static int episode,cursor,nextCapture;
        static long lastFrame,lastTick,startFrame,poseChecks,observedFrames;
        static double started,deadline,previousSeconds,lastPresented,maxPresentationGap;
        static string output;
        static readonly List<JObject> reports=new List<JObject>();
        static readonly HashSet<string> capturedFaucetPhases=new HashSet<string>();
        public static void VerifyContinuous()
        {
            KitchenDemoResearchBuild.CreateDemoScenes();KitchenDemoResearchBuild.OpenDemoPC();
            SessionState.SetBool(Key,true);SessionState.SetString(Key+"Output",KitchenDemoResearchBuild.Output());
            Begin();EditorApplication.isPlaying=true;
        }
        [InitializeOnLoadMethod] static void Resume()
        {
            EditorApplication.update-=Probe;if(SessionState.GetBool(Key,false))Begin();
        }
        static void Begin()
        {
            player=null;episode=0;capturedFaucetPhases.Clear();deadline=EditorApplication.timeSinceStartup+560;
            output=SessionState.GetString(Key+"Output","");EditorApplication.update-=Probe;EditorApplication.update+=Probe;
        }
        static void StartEpisode()
        {
            player.SelectEpisode(episode);player.SetSpeed(1);player.SetView(episode==0?1:2);
            var spec=player.Episode;
            source=KitchenDemoPlayback.Decode(Resources.Load<TextAsset>(spec.resource).bytes,spec,
                Array.ConvertAll(player.Mirror.Manifest.bodies,b=>b.id));
            shown=Array.ConvertAll(player.Mirror.Manifest.bodies,b=>player.Mirror.transform.Find(b.name));
            started=lastPresented=EditorApplication.timeSinceStartup;lastFrame=startFrame=player.AppliedFrames;
            lastTick=-1;previousSeconds=0;cursor=0;nextCapture=1;poseChecks=0;observedFrames=0;maxPresentationGap=0;player.Replay();
        }
        static void Probe()
        {
            if(!EditorApplication.isPlaying)return;
            try
            {
                if(EditorApplication.timeSinceStartup>deadline)throw new TimeoutException("Whole Demo playback exceeded finite wall budget");
                if(player==null)
                {
                    var candidate=UnityEngine.Object.FindFirstObjectByType<KitchenDemoPlayback>();
                    if(candidate==null || !candidate.IsReady)return;player=candidate;StartEpisode();return;
                }
                if(!player.IsReady || player.SelectedEpisode!=episode || player.Speed!=1 ||
                    (player.State!="PLAYING" && player.State!="ENDED"))throw new InvalidDataException("Unexpected continuous playback state");
                if(player.AppliedFrames==lastFrame)return;
                double now=EditorApplication.timeSinceStartup;
                maxPresentationGap=Math.Max(maxPresentationGap,now-lastPresented);lastPresented=now;lastFrame=player.AppliedFrames;
                if(player.Seconds<previousSeconds || player.Mirror.LastTick<lastTick)throw new InvalidDataException("Nonmonotonic display clock/tick");
                previousSeconds=player.Seconds;lastTick=player.Mirror.LastTick;
                while(cursor+1<source.Length && source[cursor+1].Seconds<=player.Seconds)cursor++;
                var a=source[cursor];var b=source[Math.Min(cursor+1,source.Length-1)];
                float alpha=b.Seconds>a.Seconds?Mathf.Clamp01((float)((player.Seconds-a.Seconds)/(b.Seconds-a.Seconds))):0;
                if(player.Mirror.LastTick!=a.Tick)throw new InvalidDataException("Displayed tick does not match lower native sample");
                for(int i=0;i<shown.Length;i++)
                {
                    var native=Vector3.Lerp(a.Positions[i],b.Positions[i],alpha);
                    var p=KitchenSceneMirror.NativeVector(new[]{native.x,native.y,native.z});
                    var q=Quaternion.Slerp(a.Rotations[i],b.Rotations[i],alpha);
                    var r=KitchenSceneMirror.NativeRotation(new[]{q.w,q.x,q.y,q.z});
                    if(shown[i]==null || Vector3.Distance(shown[i].localPosition,p)>1e-6f || Mathf.Abs(Quaternion.Dot(shown[i].localRotation,r))<1-1e-6f)
                        throw new InvalidDataException("Continuous displayed pose/source mismatch at body "+i);
                    poseChecks++;
                }
                observedFrames++;
                if(episode==0 && player.Episode.phases!=null)
                    foreach(var phase in player.Episode.phases)
                        if((phase.label=="faucet_grasp" || phase.label=="faucet_on" || phase.label=="apple_wash_state_complete" || phase.label=="faucet_off_released") &&
                            player.Seconds>=phase.time_s && capturedFaucetPhases.Add(phase.label))
                            KitchenDemoResearchBuild.Screenshot(player.DesktopCamera,Path.Combine(output,"apple_"+phase.label+".png"));
                if(nextCapture<=3 && player.Seconds>=player.Episode.duration_s*nextCapture/4)
                {
                    KitchenDemoResearchBuild.Screenshot(player.DesktopCamera,Path.Combine(output,player.Episode.id+"_quarter_"+nextCapture+".png"));
                    nextCapture++;
                }
                if(player.State!="ENDED")return;
                if(lastTick!=source[source.Length-1].Tick || player.Seconds!=player.Episode.duration_s)throw new InvalidDataException("Wrong complete-record terminal state");
                if(Math.Abs((now-started)-player.Seconds)>1)throw new InvalidDataException("1x playback wall clock mismatch");
                reports.Add(new JObject { ["episode"]=player.Episode.id,["duration_s"]=player.Seconds,["terminal_tick"]=lastTick,
                    ["wall_s"]=now-started,["applied_frames"]=player.AppliedFrames-startFrame,["observed_frames_checked"]=observedFrames,["body_pose_checks"]=poseChecks,
                    ["max_observed_frame_callback_gap_s"]=maxPresentationGap,["source_binary_sha256"]=player.Episode.sha256 });
                if(episode++==0){StartEpisode();return;}Finish(null);
            }
            catch(Exception error){Debug.LogException(error);Finish(error);}
        }
        static void Finish(Exception error)
        {
            var report=new JObject { ["status"]=error==null?"PC_DEMO_CONTINUOUS_PLAYBACK_PASS":"FAIL",["error"]=error?.ToString(),
                ["episodes"]=new JArray(reports),["evidence"]="REAL_UNITY_EDITOR_BOTH_RECORDS_1X_COMPLETE",
                ["physics"]="NO_NEW_DYNAMICS",["quest"]="HARDWARE_REQUIRED",["fps_boundary"]="EDITOR_ONLY_NOT_QUEST_PERFORMANCE",
                ["checks"]=new JArray("whole_record_at_1x","monotonic_clock_and_tick","all_observed_presented_body_poses_match_audited_samples","actual_terminal_ticks") };
            File.WriteAllText(Path.Combine(output,"editor_interaction_report.json"),report.ToString(Newtonsoft.Json.Formatting.Indented));
            SessionState.SetBool(Key,false);EditorApplication.update-=Probe;EditorApplication.Exit(error==null?0:1);
        }
    }
}
