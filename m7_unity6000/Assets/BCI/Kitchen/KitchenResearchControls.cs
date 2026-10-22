using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;

namespace BCIIntelligentRobot.Kitchen
{
    // PC-owned reset requests only; immutable base scene and read-only world mirror are unchanged.
    [DefaultExecutionOrder(200)]
    public sealed class KitchenResearchControls : MonoBehaviour
    {
        public string ServerHost="127.0.0.1";
        public int PlannerPort=11031;
        public bool Quest;
        public KitchenSceneMirror Mirror;
        public KitchenPlatformView View;
        public Camera DesktopCamera;
        public Canvas Canvas { get; private set; }
        public KitchenVrPanel VrPanel { get; private set; }
        public string ConnectionState { get; private set; }="CONNECTING";
        public string EpisodeId { get; private set; }="";
        public string InitializationHash { get; private set; }="";
        public long EpisodeIndex { get; private set; }
        public ulong Seed { get; private set; }
        public string Failure { get; private set; }="";
        public bool SafetyStopped { get; private set; }
        public string SafetyStopReason { get; private set; }="";
        public string ExecutionStatus { get; private set; }="IDLE";
        public string ExecutionReason { get; private set; }="";
        public Button NewSeedButton { get; private set; }
        public Button ExplicitSeedButton { get; private set; }
        public InputField SeedInput { get; private set; }
        public int SuccessfulResets { get; private set; }
        readonly ConcurrentQueue<JObject> incoming=new ConcurrentQueue<JObject>();
        readonly ConcurrentQueue<string> errors=new ConcurrentQueue<string>();
        readonly ConcurrentQueue<JObject> resets=new ConcurrentQueue<JObject>();
        volatile bool stopping,busy;Thread worker;TcpClient client;
        string runtime,epoch,control;
        Text stateLabel,episodeLabel,seedLabel,identityLabel,failureLabel,compactStateLabel;
        Font font;
        void Start()
        {
            BuildUI();worker=new Thread(Run){IsBackground=true,Name="KitchenResearchResetOnly"};worker.Start();
        }
        JObject Call(StreamReader reader,StreamWriter writer,string method,JObject args,JObject token,string writerId)
        {
            var request=new JObject { ["method"]=method,["args"]=args??new JObject() };
            if(method=="reset")
            {
                request["request_id"]=Guid.NewGuid().ToString("N");request["writer_id"]=writerId;request["token"]=token.DeepClone();
            }
            writer.WriteLine(request.ToString(Formatting.None));writer.Flush();
            string line=reader.ReadLine();if(line==null || line.Length>16*1024*1024)throw new IOException("Missing/oversized Research reply");
            return JObject.Parse(line);
        }
        void Run()
        {
            string writerId=Guid.NewGuid().ToString("N");
            while(!stopping)
            {
                try
                {
                    client=new TcpClient();var pending=client.ConnectAsync(ServerHost,PlannerPort);
                    if(!pending.Wait(3000))throw new TimeoutException("Research API connect timeout");pending.GetAwaiter().GetResult();
                    client.ReceiveTimeout=30000;client.SendTimeout=3000;
                    using(var reader=new StreamReader(client.GetStream()))
                    using(var writer=new StreamWriter(client.GetStream()))
                    {
                        while(!stopping)
                        {
                            JObject observed=Call(reader,writer,"observe",null,null,writerId);
                            if((bool?)observed["ok"]!=true)throw new InvalidDataException("Research observation rejected");
                            var token=observed["token"] as JObject;var episode=observed["result"]?["episode"];
                            if((string)episode?["initialization_profile"]!="research" || (string)episode?["base_scene_hash"]!=
                                "f52f3483a7d4863ead908a98aa2aae726f13b5f67e6468a1ed5323c5f3771e27" ||
                                (string)token?["episode_id"]!=(string)episode?["episode_id"] ||
                                (string)token?["initialization_hash"]!=(string)episode?["initialization_hash"])
                                throw new InvalidDataException("Research episode/base-model identity rejected");
                            incoming.Enqueue(observed);
                            if(resets.TryDequeue(out var arguments))
                            {
                                busy=true;
                                var reply=Call(reader,writer,"reset",arguments,token,writerId);
                                reply["ui_reset_reply"]=true;incoming.Enqueue(reply);busy=false;
                                // Release the reset writer so a PC external planner can acquire ownership.
                                break;
                            }
                            for(int i=0;i<5 && !stopping && resets.IsEmpty;i++)Thread.Sleep(100);
                        }
                    }
                }
                catch(Exception error){if(!stopping)errors.Enqueue(error.GetType().Name+": "+error.Message);}
                finally{client?.Close();client=null;busy=false;while(resets.TryDequeue(out _)){} }
                for(int i=0;i<20 && !stopping;i++)Thread.Sleep(100);
            }
        }
        public void ResetNewSeed(){QueueReset(new JObject());}
        public void ResetExplicitSeed()
        {
            if(!uint.TryParse(SeedInput.text,out uint seed)){Failure="INVALID SEED: uint32 required";return;}
            QueueReset(new JObject {["seed"]=seed});
        }
        void QueueReset(JObject arguments)
        {
            if(busy || !resets.IsEmpty || ConnectionState!="STREAMING")return;
            busy=true;Failure="";resets.Enqueue(arguments);
        }
        void ApplyOperationState(JToken observation)
        {
            SafetyStopped=(bool?)observation["stopped"]??false;
            SafetyStopReason=(string)observation["stop_reason"]??"";
            var execution=observation["execution"] as JObject;
            ExecutionStatus=(string)execution?["status"]??"IDLE";
            ExecutionReason=(string)execution?["reason"]??"";
        }
        void Update()
        {
            while(errors.TryDequeue(out string error)){ConnectionState="DISCONNECTED";Failure=error;}
            while(incoming.TryDequeue(out var reply))
            {
                if((bool?)reply["ok"]!=true){Failure=reply["error"]?.ToString(Formatting.None)??"RESET FAILED";continue;}
                var e=reply["result"]?["episode"];var t=reply["token"];
                if(e==null || t==null)continue;
                if((string)e["initialization_profile"]!="research" || (string)e["base_scene_hash"]!=Mirror.Manifest.model_scene_sha256 ||
                    (string)e["episode_id"]!=(string)t["episode_id"] || (string)e["initialization_hash"]!=(string)t["initialization_hash"])
                {ConnectionState="IDENTITY REJECTED";Failure="Research token/episode mismatch";continue;}
                EpisodeId=(string)e["episode_id"];InitializationHash=(string)e["initialization_hash"];EpisodeIndex=(long)e["episode_index"];Seed=(ulong)e["seed"];
                runtime=(string)t["runtime_session"];epoch=t["epoch"].ToString();control=(string)t["control_hash"];
                ApplyOperationState(reply["result"]);
                ConnectionState="SYNCING";
                if((bool?)reply["ui_reset_reply"]==true){SuccessfulResets++;Failure="";}
                if(reply["result"]?["last_reset_failure"] is JObject failure)Failure=failure.ToString(Formatting.None);
            }
            if(ConnectionState=="SYNCING" || ConnectionState=="STREAMING")
                ConnectionState=View.ConnectionState=="STREAMING" && Mirror.LastRuntimeSession==runtime && Mirror.LastEpoch==epoch?"STREAMING":"SYNCING";
            UpdateLabels();
        }
        void UpdateLabels()
        {
            if(stateLabel==null)return;
            stateLabel.text=busy?"RESETTING ON PC":SafetyStopped?"SAFETY STOP":ConnectionState+" / "+ExecutionStatus;
            if(compactStateLabel!=null)compactStateLabel.text=busy?"RESETTING":SafetyStopped?"SAFETY STOP":ConnectionState;
            episodeLabel.text="EPISODE "+EpisodeIndex+"   "+Short(EpisodeId);
            seedLabel.text="SEED "+Seed+"   /   RESEARCH PROFILE";
            identityLabel.text="INIT "+Short(InitializationHash)+"   CONTROL "+Short(control);
            string reason=SafetyStopped?SafetyStopReason:Failure;
            if(string.IsNullOrEmpty(reason) && (ExecutionStatus=="ABORTED" || ExecutionStatus=="REJECTED" || ExecutionStatus=="CANCELLED"))reason=ExecutionReason;
            failureLabel.text=reason.Length>170?reason.Substring(0,167)+"...":reason;
            NewSeedButton.interactable=ExplicitSeedButton.interactable=ConnectionState=="STREAMING" && !busy && resets.IsEmpty;
        }
        static string Short(string value)=>string.IsNullOrEmpty(value)?"--":value.Substring(0,Math.Min(16,value.Length));
        RectTransform Rect(string name,Transform parent,float x,float y,float w,float h)
        {
            var r=new GameObject(name,typeof(RectTransform)).GetComponent<RectTransform>();r.SetParent(parent,false);
            r.anchorMin=r.anchorMax=new Vector2(0,1);r.pivot=new Vector2(0,1);r.anchoredPosition=new Vector2(x,-y);r.sizeDelta=new Vector2(w,h);return r;
        }
        Text Label(Transform parent,string text,float x,float y,float w,float h,int size)
        {
            var label=Rect("ResearchLabel",parent,x,y,w,h).gameObject.AddComponent<Text>();label.font=font;label.fontSize=size;
            label.text=text;label.color=Color.white;label.raycastTarget=false;label.alignment=TextAnchor.MiddleLeft;return label;
        }
        Button MakeButton(Transform parent,string text,float x,float y,float w,UnityEngine.Events.UnityAction action)
        {
            var r=Rect(text,parent,x,y,w,44);var image=r.gameObject.AddComponent<Image>();image.color=new Color(.1f,.46f,.34f);
            var button=r.gameObject.AddComponent<Button>();button.targetGraphic=image;button.onClick.AddListener(action);
            Label(r,text,10,0,w-20,44,19).alignment=TextAnchor.MiddleCenter;return button;
        }
        void BuildUI()
        {
            font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            var canvas=new GameObject("KitchenResearchCanvas",typeof(RectTransform)).AddComponent<Canvas>();canvas.sortingOrder=30;
            Canvas=canvas;OVRCameraRig rig=null;
            if(Quest)
            {
                canvas.renderMode=RenderMode.WorldSpace;var r=(RectTransform)canvas.transform;r.sizeDelta=new Vector2(1280,220);
                rig=FindFirstObjectByType<OVRCameraRig>();
            }
            else
            {
                canvas.renderMode=RenderMode.ScreenSpaceCamera;canvas.worldCamera=DesktopCamera;canvas.planeDistance=.5f;
                var scale=canvas.gameObject.AddComponent<CanvasScaler>();scale.uiScaleMode=CanvasScaler.ScaleMode.ScaleWithScreenSize;scale.referenceResolution=new Vector2(1440,900);scale.matchWidthOrHeight=.5f;
            }
            canvas.gameObject.AddComponent<GraphicRaycaster>();
            if(FindFirstObjectByType<EventSystem>()==null){var e=new GameObject("ResearchEventSystem");e.AddComponent<EventSystem>();e.AddComponent<InputSystemUIInputModule>();}
            var panel=Rect("ResearchConsole",canvas.transform,0,0,1280,220);
            if(!Quest){panel.anchorMin=panel.anchorMax=new Vector2(.5f,0);panel.pivot=new Vector2(.5f,0);panel.anchoredPosition=new Vector2(0,14);}
            panel.gameObject.AddComponent<Image>().color=new Color(.055f,.075f,.078f);
            Label(panel,"KITCHEN RESEARCH",18,8,330,36,25);
            Label(panel,"LIVE PC MUJOCO STATE",360,8,500,36,23);
            if(!Quest)Label(panel,"QUEST NOT VERIFIED",970,8,292,36,19).color=new Color(1,.72f,.37f);
            NewSeedButton=MakeButton(panel,"NEW EPISODE",18,55,230,ResetNewSeed);
            var input=Rect("ExplicitUint32Seed",panel,265,55,260,44);input.gameObject.AddComponent<Image>().color=new Color(.94f,.96f,.95f);
            SeedInput=input.gameObject.AddComponent<InputField>();var text=Label(input,"0",12,0,236,44,22);text.color=Color.black;
            SeedInput.textComponent=text;SeedInput.contentType=InputField.ContentType.IntegerNumber;SeedInput.text="0";SeedInput.characterLimit=10;
            ExplicitSeedButton=MakeButton(panel,"RESET SEED",542,55,200,ResetExplicitSeed);
            stateLabel=Label(panel,"CONNECTING",775,55,480,44,21);
            episodeLabel=Label(panel,"",18,111,585,29,20);seedLabel=Label(panel,"",625,111,635,29,20);
            identityLabel=Label(panel,"",18,146,1240,27,17);failureLabel=Label(panel,"",18,180,1240,31,16);failureLabel.color=new Color(1,.66f,.45f);
            if(Quest)
            {
                VrPanel=gameObject.AddComponent<KitchenVrPanel>();
                MakeButton(panel,"HIDE",970,6,138,()=>VrPanel.SetExpanded(false));
                MakeButton(panel,"RECALL",1124,6,138,()=>VrPanel.Recall());
                var compact=Rect("ResearchCompact",canvas.transform,0,0,560,64);
                compact.anchorMin=compact.anchorMax=compact.pivot=new Vector2(.5f,.5f);compact.anchoredPosition=Vector2.zero;
                compact.gameObject.AddComponent<Image>().color=new Color(.055f,.075f,.078f);
                compactStateLabel=Label(compact,ConnectionState,16,10,364,44,19);
                MakeButton(compact,"MENU",398,10,146,()=>VrPanel.Recall());
                VrPanel.Configure(canvas,rig,panel,compact);
                var ray=gameObject.AddComponent<KitchenDemoRayInput>();ray.Canvas=canvas;ray.MaxDistance=3;
            }
        }
        void OnDestroy(){stopping=true;client?.Close();worker?.Join(250);}
    }
}
