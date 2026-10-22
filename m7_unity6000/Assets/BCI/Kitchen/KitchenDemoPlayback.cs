using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace BCIIntelligentRobot.Kitchen
{
    [Serializable] public sealed class DemoPhase { public string label; public double time_s; public long tick; }
    [Serializable] public sealed class DemoWaterEvent { public double time_s; public long tick; public bool water_on; }
    [Serializable] public sealed class DemoEpisode
    {
        public string id,title,resource,sha256,terminal_state,source_record_sha256,source_report_sha256;
        public string record_final_chain_sha256,provenance_sha256,control_hash;
        public long terminal_tick;
        public int frame_count,body_count; public double duration_s,first_time_s;
        public DemoPhase[] phases; public DemoWaterEvent[] water_events;
    }
    [Serializable] public sealed class DemoCatalog
    {
        public int schema; public string label,model_resource,scene_hash,compiled_model_hash,control_hash,manifest_sha256;
        public DemoEpisode[] episodes;
    }
    [Serializable] sealed class DemoProvenance
    {
        public string status,hash_chain,scene_hash,compiled_model_hash,control_hash,source_record_sha256,source_report_sha256;
        public long source_physics_samples;
    }
    public sealed class DemoPoseFrame
    {
        public double Seconds; public long Tick; public uint Flags; public float CleanSeconds,HeatSeconds;
        public Vector3[] Positions; public Quaternion[] Rotations;
    }

    // Presentation of immutable native records only. This component has no network or physics authority.
    [DefaultExecutionOrder(100)]
    public sealed class KitchenDemoPlayback : MonoBehaviour
    {
        public KitchenSceneMirror Mirror;
        public Camera DesktopCamera,RecordingCamera;
        public bool Quest;
        public string CatalogResource="KitchenDemoV3/catalog";
        public DemoCatalog Catalog { get; private set; }
        public bool IsReady => Catalog!=null && world!=null && recordings.Count==Catalog.episodes.Length && State!="IDENTITY REJECTED";
        public DemoEpisode Episode => Catalog.episodes[SelectedEpisode];
        public int SelectedEpisode { get; private set; }
        public double Seconds { get; private set; }
        public float Speed { get; private set; } = 1;
        public string State { get; private set; } = "LOADING";
        public string Error { get; private set; } = "";
        public int ViewIndex { get; private set; }
        public long AppliedFrames { get; private set; }
        public uint DeviceFlags { get; private set; }
        public long RecordedTerminalTick => recordings[SelectedEpisode][recordings[SelectedEpisode].Length-1].Tick;
        public string RecordedControlHash => Catalog.schema==1?Catalog.control_hash:Episode.control_hash;
        public float CleanSeconds { get; private set; }
        public float HeatSeconds { get; private set; }
        public string LastCapture { get; private set; } = "";
        readonly List<DemoPoseFrame[]> recordings = new List<DemoPoseFrame[]>();
        KitchenWorld world; int cursor,epoch; double playbackClock;
        public static readonly string[] Views = {"OVERVIEW","SINK","MICROWAVE","TRAY"};
        static readonly Vector3[] Positions = {
            new Vector3(2.55f,2.65f,-3.35f),new Vector3(-1.48f,1.62f,-.72f),
            new Vector3(.08f,1.50f,-1.18f),new Vector3(.15f,1.85f,-1.7f)};
        static readonly Vector3[] Targets = {
            new Vector3(0,.8f,.02f),new Vector3(-.83f,.80f,.10f),
            new Vector3(.82f,1.02f,-.08f),new Vector3(0,.82f,-.37f)};

        public static string Hash(byte[] bytes)
        {
            using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();
        }
        public static bool SupportedCatalog(DemoCatalog catalog)
        {
            const string original="2ff69af80d5c6fc0724382dcd478ffa2e0699a227153c46d263a368c5b246155";
            const string side="b6722bb912fddedf853d54925fda092b8ea2833cbcaeb904040ecde5965335d2";
            if(catalog==null || catalog.episodes==null || catalog.episodes.Length!=2)return false;
            if(catalog.schema==1)return catalog.control_hash==original;
            return catalog.schema==2 && catalog.control_hash=="PER_EPISODE_RECORDED_CONTROL" &&
                catalog.episodes[0].id=="apple" && catalog.episodes[0].control_hash==side &&
                catalog.episodes[1].id=="burger" && catalog.episodes[1].control_hash==original &&
                catalog.episodes[0].terminal_tick>0 && catalog.episodes[1].terminal_tick==64160;
        }
        public static DemoPoseFrame[] Decode(byte[] bytes,DemoEpisode episode,int[] expectedIds)
        {
            if(Hash(bytes)!=episode.sha256)throw new InvalidDataException("Replay SHA256 rejected");
            using(var reader=new BinaryReader(new MemoryStream(bytes)))
            {
                if(Encoding.ASCII.GetString(reader.ReadBytes(8))!="KDPREC01")throw new InvalidDataException("Replay magic rejected");
                int count=reader.ReadInt32(),bodies=reader.ReadInt32();
                if(count!=episode.frame_count || bodies!=episode.body_count || bodies!=expectedIds.Length ||
                    bytes.Length!=16+4*bodies+(long)count*(28+28*bodies))throw new InvalidDataException("Replay size rejected");
                for(int i=0;i<bodies;i++)if(reader.ReadInt32()!=expectedIds[i])throw new InvalidDataException("Replay body map rejected");
                var result=new DemoPoseFrame[count]; double prior=-1; long priorTick=-1;
                for(int f=0;f<count;f++)
                {
                    var frame=new DemoPoseFrame {Seconds=reader.ReadDouble(),Tick=reader.ReadInt64(),Flags=reader.ReadUInt32(),
                        CleanSeconds=reader.ReadSingle(),HeatSeconds=reader.ReadSingle(),Positions=new Vector3[bodies],Rotations=new Quaternion[bodies]};
                    if(double.IsNaN(frame.Seconds) || double.IsInfinity(frame.Seconds) || frame.Seconds<=prior || frame.Tick<=priorTick)
                        throw new InvalidDataException("Replay ordering rejected");
                    prior=frame.Seconds;priorTick=frame.Tick;
                    for(int b=0;b<bodies;b++)
                    {
                        frame.Positions[b]=new Vector3(reader.ReadSingle(),reader.ReadSingle(),reader.ReadSingle());
                        float w=reader.ReadSingle();var q=new Quaternion(reader.ReadSingle(),reader.ReadSingle(),reader.ReadSingle(),w);
                        float norm=Quaternion.Dot(q,q);
                        if(float.IsNaN(frame.Positions[b].sqrMagnitude) || float.IsInfinity(frame.Positions[b].sqrMagnitude) ||
                            float.IsNaN(norm) || float.IsInfinity(norm) || Mathf.Abs(norm-1)>.001f)
                            throw new InvalidDataException("Replay nonfinite pose rejected");
                        frame.Rotations[b]=q.normalized;
                    }
                    result[f]=frame;
                }
                return result;
            }
        }
        void Start()
        {
            try { Initialize(); }
            catch(Exception error) { Debug.LogException(error); }
        }
        public void Initialize()
        {
            if(IsReady || State=="IDENTITY REJECTED")return;
            try { InitializeVerified(); }
            catch(Exception error)
            {
                Catalog=null;world=null;recordings.Clear();Error=error.Message;State="IDENTITY REJECTED";
                throw;
            }
        }
        void InitializeVerified()
        {
            if(!Mirror.Built)Mirror.Build();
            GetComponent<KitchenPlatformArt>().Apply();
            var asset=Resources.Load<TextAsset>(CatalogResource);
            if(asset==null)throw new InvalidDataException("Demo catalog missing");
            var catalog=JsonUtility.FromJson<DemoCatalog>(asset.text);
            if(catalog==null || catalog.episodes==null)throw new InvalidDataException("Demo catalog invalid");
            var manifest=Resources.Load<TextAsset>(catalog.model_resource+"/scene");
            if(manifest==null || !SupportedCatalog(catalog) || Hash(manifest.bytes)!=catalog.manifest_sha256 ||
                catalog.scene_hash!=Mirror.Manifest.model_scene_sha256 || catalog.compiled_model_hash!=Mirror.Manifest.model_compiled_sha256 ||
                !CatalogResource.EndsWith("/catalog",StringComparison.Ordinal))
                throw new InvalidDataException("Demo/model identity rejected");
            int[] ids=Array.ConvertAll(Mirror.Manifest.bodies,b=>b.id);
            var verified=new List<DemoPoseFrame[]>();
            foreach(var episode in catalog.episodes)
            {
                string resourceRoot=CatalogResource.Substring(0,CatalogResource.LastIndexOf('/'));
                var provenance=Resources.Load<TextAsset>(resourceRoot+"/"+episode.id+"_provenance");
                var record=Resources.Load<TextAsset>(episode.resource);
                if(provenance==null || Hash(provenance.bytes)!=episode.provenance_sha256)
                    throw new InvalidDataException("Provenance SHA256 rejected");
                var identity=JsonUtility.FromJson<DemoProvenance>(provenance.text);
                if(identity==null || identity.hash_chain!="PASS" || identity.status!="SAME_RECORD_KINEMATIC_PACKET_DERIVATION_PASS" ||
                    identity.scene_hash!=catalog.scene_hash || identity.compiled_model_hash!=catalog.compiled_model_hash ||
                    identity.control_hash!=(catalog.schema==1?catalog.control_hash:episode.control_hash) ||
                    identity.source_record_sha256!=episode.source_record_sha256 || identity.source_report_sha256!=episode.source_report_sha256)
                    throw new InvalidDataException("Per-episode recorded identity rejected");
                if(record==null)throw new InvalidDataException("Demo record missing");
                var decoded=Decode(record.bytes,episode,ids);
                if(decoded[decoded.Length-1].Tick!=identity.source_physics_samples ||
                    (catalog.schema==2 && decoded[decoded.Length-1].Tick!=episode.terminal_tick))
                    throw new InvalidDataException("Recorded terminal tick rejected");
                verified.Add(decoded);
            }
            var bodies=new KitchenBody[ids.Length];
            for(int i=0;i<ids.Length;i++)bodies[i]=new KitchenBody {id=ids[i],name=Mirror.Manifest.bodies[i].name,position=new float[3],quaternion_wxyz=new float[4]};
            recordings.AddRange(verified);Catalog=catalog;
            world=new KitchenWorld {schema=1,type="world",runtimeSession="offline-demo",sceneHash=catalog.scene_hash,bodies=bodies};
            SelectEpisode(0);SetView(0);Debug.Log("KITCHEN_DEMO_READY offline=1 episodes="+recordings.Count);
        }
        public void SelectEpisode(int index)
        {
            if(!IsReady)return;
            if(index<0 || index>=recordings.Count)throw new ArgumentOutOfRangeException(nameof(index));
            SelectedEpisode=index;State="READY";Seconds=0;cursor=0;epoch++;Apply();playbackClock=Time.realtimeSinceStartupAsDouble;
        }
        public void TogglePlay()
        {
            if(!IsReady)return;
            if(State=="PLAYING")
            {
                AdvanceClock(Time.realtimeSinceStartupAsDouble);
                if(State=="PLAYING")State="PAUSED";
            }
            else {if(Seconds>=Episode.duration_s){Seconds=0;cursor=0;epoch++;}playbackClock=Time.realtimeSinceStartupAsDouble;State="PLAYING";}
        }
        public void Replay() { if(!IsReady)return;Seconds=0;cursor=0;epoch++;State="PLAYING";Apply();playbackClock=Time.realtimeSinceStartupAsDouble; }
        public void Seek(double seconds)
        {
            if(!IsReady)return;
            if(double.IsNaN(seconds) || double.IsInfinity(seconds))throw new ArgumentOutOfRangeException(nameof(seconds));
            Seconds=Math.Clamp(seconds,0,Episode.duration_s);cursor=0;epoch++;
            if(State=="ENDED")State="PAUSED";Apply();playbackClock=Time.realtimeSinceStartupAsDouble;
        }
        public void SetSpeed(float speed)
        {
            if(speed!=.5f && speed!=1 && speed!=2 && speed!=4)throw new ArgumentOutOfRangeException(nameof(speed));
            if(IsReady && State=="PLAYING")AdvanceClock(Time.realtimeSinceStartupAsDouble);
            Speed=speed;
        }
        public string Phase
        {
            get {if(!IsReady)return Error;string phase="initial recorded state";foreach(var value in Episode.phases)if(value.time_s<=Seconds)phase=value.label;return phase.Replace('_',' ');}
        }
        public void SetView(int index)
        {
            ViewIndex=Math.Clamp(index,0,Views.Length-1);
            foreach(var camera in new[]{RecordingCamera,Quest?null:DesktopCamera})
                if(camera!=null){camera.transform.position=Positions[ViewIndex];camera.transform.LookAt(Targets[ViewIndex]);camera.fieldOfView=ViewIndex==0?44:48;}
        }
        void Update()
        {
            if(!IsReady || State!="PLAYING")return;
            AdvanceClock(Time.realtimeSinceStartupAsDouble);
        }
        void AdvanceClock(double now)
        {
            // The interval starts at the playback command, not the previous Unity frame.
            Seconds=Math.Min(Episode.duration_s,Seconds+Math.Max(0,now-playbackClock)*Speed);playbackClock=now;
            Apply();if(Seconds>=Episode.duration_s)State="ENDED";
        }
        void Apply()
        {
            var frames=recordings[SelectedEpisode];
            while(cursor+1<frames.Length && frames[cursor+1].Seconds<=Seconds)cursor++;
            var a=frames[cursor];var b=frames[Math.Min(cursor+1,frames.Length-1)];
            float alpha=b.Seconds>a.Seconds?Mathf.Clamp01((float)((Seconds-a.Seconds)/(b.Seconds-a.Seconds))):0;
            for(int i=0;i<world.bodies.Length;i++)
            {
                var p=Vector3.Lerp(a.Positions[i],b.Positions[i],alpha);var q=Quaternion.Slerp(a.Rotations[i],b.Rotations[i],alpha);
                var body=world.bodies[i];body.position[0]=p.x;body.position[1]=p.y;body.position[2]=p.z;
                body.quaternion_wxyz[0]=q.w;body.quaternion_wxyz[1]=q.x;body.quaternion_wxyz[2]=q.y;body.quaternion_wxyz[3]=q.z;
            }
            world.sceneEpoch=Episode.id+"-seek-"+epoch;world.simTick=a.Tick;world.sequence=++AppliedFrames;Mirror.Apply(world);
            DeviceFlags=a.Flags;CleanSeconds=a.CleanSeconds;HeatSeconds=a.HeatSeconds;
            if(Episode.water_events.Length>0)
            {
                bool on=false;foreach(var e in Episode.water_events)if(e.time_s<=Seconds)on=e.water_on;
                DeviceFlags=on?(DeviceFlags|1u):(DeviceFlags&~1u);
            }
        }
        [Serializable] sealed class CaptureInfo
        {
            public string mode="OFFLINE VERIFIED MUJOCO RECORD",episode,view,source_record_sha256,scene_hash,control_hash,png_sha256;
            public string quest="HARDWARE_REQUIRED";public double time_s;public long lower_record_tick;
        }
        public string Capture(string directory=null)
        {
            if(!IsReady)throw new InvalidOperationException("Demo record is not verified");
            directory=directory??Path.Combine(Application.persistentDataPath,"KitchenDemoCaptures");Directory.CreateDirectory(directory);
            string path=Path.Combine(directory,"demo_"+Episode.id+"_"+DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff")+".png");
            var old=RecordingCamera.targetTexture;var active=RenderTexture.active;
            var rt=new RenderTexture(1600,1000,24);var image=new Texture2D(1600,1000,TextureFormat.RGB24,false);
            try
            {
                RecordingCamera.targetTexture=rt;RecordingCamera.Render();RenderTexture.active=rt;
                image.ReadPixels(new Rect(0,0,1600,1000),0,0);image.Apply();byte[] png=image.EncodeToPNG();File.WriteAllBytes(path,png);
                File.WriteAllText(Path.ChangeExtension(path,".json"),JsonUtility.ToJson(new CaptureInfo {episode=Episode.id,view=Views[ViewIndex],
                    source_record_sha256=Episode.source_record_sha256,scene_hash=Catalog.scene_hash,control_hash=RecordedControlHash,
                    time_s=Seconds,lower_record_tick=world.simTick,png_sha256=Hash(png)},true));LastCapture=path;return path;
            }
            finally {RecordingCamera.targetTexture=old;RenderTexture.active=active;rt.Release();Destroy(rt);Destroy(image);}
        }
    }
}
