using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using UnityEngine.InputSystem.UI;

namespace BCIIntelligentRobot.Kitchen
{
    [DefaultExecutionOrder(200)]
    public sealed class KitchenDemoUI : MonoBehaviour
    {
        public KitchenDemoPlayback Player;
        public Canvas Canvas { get; private set; }
        public Button[] ActionButtons { get; private set; }
        public Button[] ViewButtons { get; private set; }
        public Button PlayButton { get; private set; }
        public Button ReplayButton { get; private set; }
        public Button CaptureButton { get; private set; }
        public Slider Timeline { get; private set; }
        public KitchenVrPanel VrPanel { get; private set; }
        public Button CollapseButton { get; private set; }
        public Button ExpandButton { get; private set; }
        public Button CompactPlayButton { get; private set; }
        public Button[] SpeedButtons { get; private set; }
        Text status,phase,timing,device,playLabel,captureStatus,compactStatus,compactPlayLabel;
        Font font; bool built;float nextRefresh;string shownState;int shownEpisode=-1;
        static readonly Color Ink=new Color(.055f,.075f,.078f), Paper=new Color(.94f,.96f,.95f);
        static readonly Color Accent=new Color(.10f,.50f,.34f), Neutral=new Color(.21f,.25f,.26f);
        public static readonly Vector3 SeatedEye=KitchenVrPanel.SeatedEye;

        // Move the rig parent once; tracked camera and controller local poses remain untouched.
        public static void AlignSeatedOrigin(Transform origin,Transform head)
        {
            KitchenVrPanel.AlignSeatedOrigin(origin,head);
        }

        RectTransform Box(string name,Transform parent,float x,float y,float w,float h)
        {
            var rect=new GameObject(name,typeof(RectTransform)).GetComponent<RectTransform>();rect.SetParent(parent,false);
            rect.anchorMin=rect.anchorMax=new Vector2(0,1);rect.pivot=new Vector2(0,1);
            rect.anchoredPosition=new Vector2(x,-y);rect.sizeDelta=new Vector2(w,h);return rect;
        }
        Text Label(string name,Transform parent,string value,float x,float y,float w,float h,int size,Color color)
        {
            var text=Box(name,parent,x,y,w,h).gameObject.AddComponent<Text>();text.text=value;text.font=font;text.fontSize=size;
            text.color=color;text.alignment=TextAnchor.MiddleLeft;text.raycastTarget=false;
            text.horizontalOverflow=HorizontalWrapMode.Wrap;text.verticalOverflow=VerticalWrapMode.Truncate;return text;
        }
        Button Button(string name,Transform parent,string value,float x,float y,float w,Action action)
        {
            var rect=Box(name,parent,x,y,w,42);var image=rect.gameObject.AddComponent<Image>();image.color=Neutral;
            var button=rect.gameObject.AddComponent<Button>();button.targetGraphic=image;
            var colors=button.colors;colors.highlightedColor=new Color(.65f,.84f,.76f);colors.pressedColor=new Color(.45f,.72f,.6f);button.colors=colors;
            var label=Label(name+"Label",rect,value,7,0,w-14,42,18,Color.white);label.alignment=TextAnchor.MiddleCenter;
            button.onClick.AddListener(()=>action());return button;
        }
        void Start() { Build(); }
        public void Build()
        {
            if(built || (Player.Catalog==null && Player.State!="IDENTITY REJECTED"))return;
            font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            var root=new GameObject("DemoCanvas",typeof(RectTransform));root.transform.SetParent(transform,false);
            Canvas=root.AddComponent<Canvas>();Canvas.sortingOrder=30;
            if(Player.Quest)
            {
                Canvas.renderMode=RenderMode.WorldSpace;
                var rect=(RectTransform)Canvas.transform;rect.sizeDelta=new Vector2(1280,228);
                var rig=FindFirstObjectByType<OVRCameraRig>();Canvas.worldCamera=rig.centerEyeAnchor.GetComponent<Camera>();
            }
            else
            {
                Canvas.renderMode=RenderMode.ScreenSpaceCamera;Canvas.worldCamera=Player.DesktopCamera;Canvas.planeDistance=.5f;
                var scale=root.AddComponent<CanvasScaler>();scale.uiScaleMode=CanvasScaler.ScaleMode.ScaleWithScreenSize;
                scale.referenceResolution=new Vector2(1440,900);scale.matchWidthOrHeight=.5f;
            }
            root.AddComponent<GraphicRaycaster>();
            var events=FindFirstObjectByType<EventSystem>();
            if(events==null){var eventObject=new GameObject("DemoEventSystem");events=eventObject.AddComponent<EventSystem>();eventObject.AddComponent<InputSystemUIInputModule>();}
            var panel=Box("PlaybackConsole",root.transform,0,0,1280,228);
            if(!Player.Quest)
            {
                panel.anchorMin=new Vector2(.5f,0);panel.anchorMax=new Vector2(.5f,0);panel.pivot=new Vector2(.5f,0);
                panel.anchoredPosition=new Vector2(0,14);
            }
            panel.gameObject.AddComponent<Image>().color=Ink;
            Label("Product",panel,"KITCHEN DEMO",18,8,215,31,25,Paper);
            Label("Evidence",panel,"OFFLINE VERIFIED MUJOCO RECORD",245,8,590,31,23,new Color(.51f,.85f,.67f));
            Label("Boundary",panel,"QUEST RECHECK",981,8,210,31,19,new Color(1,.72f,.37f));
            ActionButtons=new[] {
                Button("ActionApple",panel,"APPLE / WASH / TRAY",18,50,260,()=>Player.SelectEpisode(0)),
                Button("ActionBurger",panel,"BURGER / MICROWAVE",288,50,260,()=>Player.SelectEpisode(1))};
            PlayButton=Button("PlayPause",panel,"\u25b6",566,50,58,()=>TogglePlay());playLabel=PlayButton.GetComponentInChildren<Text>();
            ReplayButton=Button("Replay",panel,"\u21bb",632,50,58,()=>{Player.Replay();VrPanel?.SetExpanded(false);});
            CaptureButton=Button("CaptureRecordingView",panel,"CAPTURE",1090,50,170,()=>Capture());
            SpeedButtons=new Button[3];float[] speeds={1,2,4};
            for(int i=0;i<speeds.Length;i++){float speed=speeds[i];SpeedButtons[i]=Button("Speed"+speed,panel,speed+"x",704+i*76,50,66,()=>{Player.SetSpeed(speed);nextRefresh=0;});}
            status=Label("PlaybackStatus",panel,"READY",946,50,138,42,19,Paper);
            var track=Box("Timeline",panel,20,103,1240,25);var back=track.gameObject.AddComponent<Image>();back.color=Neutral;
            Timeline=track.gameObject.AddComponent<Slider>();Timeline.minValue=0;Timeline.maxValue=1;Timeline.targetGraphic=back;
            var fillArea=Box("FillArea",track,7,4,1226,17);
            var fill=Box("Fill",fillArea,0,0,0,0);fill.anchorMin=Vector2.zero;fill.anchorMax=Vector2.one;
            fill.offsetMin=fill.offsetMax=Vector2.zero;fill.gameObject.AddComponent<Image>().color=Accent;
            var handleArea=Box("HandleArea",track,7,-3,1226,31);
            var handle=Box("Handle",handleArea,0,0,14,0);handle.anchorMin=Vector2.zero;handle.anchorMax=new Vector2(0,1);
            handle.pivot=new Vector2(.5f,.5f);handle.anchoredPosition=Vector2.zero;handle.sizeDelta=new Vector2(14,0);
            handle.gameObject.AddComponent<Image>().color=Paper;
            Timeline.fillRect=fill;Timeline.handleRect=handle;Timeline.direction=Slider.Direction.LeftToRight;
            Timeline.onValueChanged.AddListener(value=>{if(Player.IsReady)Player.Seek(value*Player.Episode.duration_s);});
            timing=Label("Time",panel,"",20,133,330,25,18,Paper);
            phase=Label("Phase",panel,"",355,133,660,25,18,Paper);
            device=Label("DeviceState",panel,"",683,198,577,23,14,new Color(.74f,.84f,.78f));
            ViewButtons=new Button[4];
            for(int i=0;i<ViewButtons.Length;i++){int index=i;ViewButtons[i]=Button("View"+i,panel,KitchenDemoPlayback.Views[i],20+i*161,173,151,()=>Player.SetView(index));}
            captureStatus=Label("CaptureStatus",panel,"STATE-MODEL WASH / HEAT",683,173,577,21,14,new Color(.74f,.84f,.78f));
            if(Player.Quest)
            {
                VrPanel=gameObject.AddComponent<KitchenVrPanel>();
                CollapseButton=Button("CollapsePanel",panel,"\u2212",1202,3,58,()=>VrPanel.SetExpanded(false));
                var compact=Box("CompactDemoControls",root.transform,0,0,420,64);
                compact.anchorMin=compact.anchorMax=compact.pivot=new Vector2(.5f,.5f);compact.anchoredPosition=Vector2.zero;
                compact.gameObject.AddComponent<Image>().color=Ink;
                ExpandButton=Button("OpenMenu",compact,"MENU",8,11,94,()=>VrPanel.Recall());
                CompactPlayButton=Button("CompactPlayPause",compact,"\u25b6",110,11,58,()=>TogglePlay());
                compactPlayLabel=CompactPlayButton.GetComponentInChildren<Text>();
                Button("CompactReplay",compact,"\u21bb",176,11,58,()=>Player.Replay());
                compactStatus=Label("CompactTime",compact,"",246,9,167,44,18,Paper);
                VrPanel.Configure(Canvas,FindFirstObjectByType<OVRCameraRig>(),panel,compact);
                var ray=gameObject.AddComponent<KitchenDemoRayInput>();ray.Canvas=Canvas;ray.MaxDistance=3;
            }
            built=true;
        }
        void TogglePlay(){Player.TogglePlay();if(Player.State=="PLAYING")VrPanel?.SetExpanded(false);}
        void Capture()
        {
            try{Player.Capture();captureStatus.text="CAPTURE SAVED / OFFLINE RECORD";}
            catch(Exception error){captureStatus.text="CAPTURE FAILED";Debug.LogException(error);}
        }
        void Update()
        {
            if(!built){Build();return;}
            if(Time.unscaledTime<nextRefresh && shownState==Player.State && shownEpisode==Player.SelectedEpisode)return;
            nextRefresh=Time.unscaledTime+.1f;shownState=Player.State;shownEpisode=Player.SelectedEpisode;
            if(!Player.IsReady)
            {
                status.text=Player.State;phase.text=Player.Error;timing.text="--";device.text="";
                PlayButton.interactable=ReplayButton.interactable=CaptureButton.interactable=Timeline.interactable=false;
                foreach(var button in ActionButtons)button.interactable=false;
                foreach(var button in ViewButtons)button.interactable=false;
                foreach(var button in SpeedButtons)button.interactable=false;
                if(CompactPlayButton!=null)CompactPlayButton.interactable=false;
                return;
            }
            status.text=Player.State;playLabel.text=Player.State=="PLAYING"?"\u275a\u275a":"\u25b6";
            Timeline.SetValueWithoutNotify((float)(Player.Seconds/Player.Episode.duration_s));
            timing.text=TimeSpan.FromSeconds(Player.Seconds).ToString(@"mm\:ss")+" / "+TimeSpan.FromSeconds(Player.Episode.duration_s).ToString(@"mm\:ss")+"   "+Player.Speed+"x REPLAY";
            if(compactStatus!=null){compactStatus.text=TimeSpan.FromSeconds(Player.Seconds).ToString(@"mm\:ss")+"  "+Player.Speed+"x";compactPlayLabel.text=playLabel.text;}
            phase.text=Player.Phase;
            device.text=Player.Episode.id=="apple"?"WATER "+((Player.DeviceFlags&1)!=0?"ON":"OFF")+"   WASH "+Player.CleanSeconds.ToString("F1")+" s (STATE MODEL)":
                "HEAT "+Player.HeatSeconds.ToString("F1")+" s   "+((Player.DeviceFlags&8)!=0?"HEATED (STATE MODEL)":(Player.DeviceFlags&2)!=0?"HEATING (STATE MODEL)":"STATE MODEL");
            for(int i=0;i<ActionButtons.Length;i++)ActionButtons[i].GetComponent<Image>().color=i==Player.SelectedEpisode?Accent:Neutral;
            for(int i=0;i<ViewButtons.Length;i++)ViewButtons[i].GetComponent<Image>().color=i==Player.ViewIndex?Accent:Neutral;
            for(int i=0;i<SpeedButtons.Length;i++)SpeedButtons[i].GetComponent<Image>().color=Player.Speed==(1<<i)?Accent:Neutral;
            if(Player.Seconds>=Player.Episode.duration_s)phase.text=Player.Episode.terminal_state.Replace('_',' ');
        }
    }

    // Existing Meta rig and Touch tracking; ray only drives the offline console.
    public sealed class KitchenDemoRayInput : MonoBehaviour
    {
        public Canvas Canvas;
        public float MaxDistance=4;
        OVRCameraRig rig;LineRenderer line;GameObject hovered,pressed;
        PointerEventData pointer;readonly List<RaycastResult> hits=new List<RaycastResult>();
        void Start()
        {
            rig=FindFirstObjectByType<OVRCameraRig>();pointer=new PointerEventData(EventSystem.current);
            line=new GameObject("DemoTouchRay").AddComponent<LineRenderer>();line.positionCount=2;line.startWidth=.002f;line.endWidth=.001f;
            line.material=new Material(Shader.Find("Sprites/Default"));line.startColor=new Color(.2f,.9f,.6f);line.endColor=Color.white;
        }
        void Update()
        {
            bool right=OVRInput.GetControllerPositionTracked(OVRInput.Controller.RTouch);
            var hand=right?rig.rightControllerAnchor:rig.leftControllerAnchor;
            var controller=right?OVRInput.Controller.RTouch:OVRInput.Controller.LTouch;
            bool tracked=OVRInput.GetControllerPositionTracked(controller);line.enabled=tracked && Canvas!=null && Canvas.isActiveAndEnabled;if(!line.enabled)return;
            var ray=new Ray(hand.position,hand.forward);var plane=new Plane(Canvas.transform.forward,Canvas.transform.position);
            float distance;bool intersects=plane.Raycast(ray,out distance) && distance<MaxDistance;
            var end=ray.GetPoint(intersects?distance:2);line.SetPosition(0,ray.origin);line.SetPosition(1,end);
            pointer.position=RectTransformUtility.WorldToScreenPoint(Canvas.worldCamera,end);hits.Clear();
            if(intersects)EventSystem.current.RaycastAll(pointer,hits);
            GameObject target=hits.Count>0?hits[0].gameObject:null;
            if(target!=hovered)
            {
                if(hovered!=null)ExecuteEvents.Execute(hovered,pointer,ExecuteEvents.pointerExitHandler);
                if(target!=null)ExecuteEvents.Execute(target,pointer,ExecuteEvents.pointerEnterHandler);hovered=target;
            }
            if(OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger,controller) && target!=null)
            {
                pointer.pressPosition=pointer.position;pressed=ExecuteEvents.ExecuteHierarchy(target,pointer,ExecuteEvents.pointerDownHandler);
                pointer.pointerPress=pressed;pointer.pointerDrag=ExecuteEvents.GetEventHandler<IDragHandler>(target);
                if(pointer.pointerDrag!=null)ExecuteEvents.Execute(pointer.pointerDrag,pointer,ExecuteEvents.initializePotentialDrag);
            }
            if(OVRInput.Get(OVRInput.Button.PrimaryIndexTrigger,controller) && pointer.pointerDrag!=null)
                ExecuteEvents.Execute(pointer.pointerDrag,pointer,ExecuteEvents.dragHandler);
            if(OVRInput.GetUp(OVRInput.Button.PrimaryIndexTrigger,controller))
            {
                if(pressed!=null)
                {
                    ExecuteEvents.Execute(pressed,pointer,ExecuteEvents.pointerUpHandler);
                    if(ExecuteEvents.GetEventHandler<IPointerClickHandler>(target)==pressed)ExecuteEvents.Execute(pressed,pointer,ExecuteEvents.pointerClickHandler);
                }
                if(pointer.pointerDrag!=null)ExecuteEvents.Execute(pointer.pointerDrag,pointer,ExecuteEvents.endDragHandler);
                pressed=null;pointer.pointerDrag=null;pointer.pointerPress=null;
            }
        }
        void OnDestroy(){if(line!=null){Destroy(line.material);Destroy(line.gameObject);}}
    }
}
