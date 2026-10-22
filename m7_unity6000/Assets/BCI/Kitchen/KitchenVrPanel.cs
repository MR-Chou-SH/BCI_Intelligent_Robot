using System.Collections;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.UI;

namespace BCIIntelligentRobot.Kitchen
{
    // Shared presentation only. Neither the tracked head nor the PC-owned world is driven here.
    public sealed class KitchenVrPanel : MonoBehaviour
    {
        public static readonly Vector3 SeatedEye=new Vector3(0,1.20f,-1.16f);
        public const float PanelDistance=1.15f;
        public bool Expanded { get; private set; }=true;
        Canvas canvas;OVRCameraRig rig;RectTransform full,compact;Material overlay;

        public static void AlignSeatedOrigin(Transform origin,Transform head)
        {
            Vector3 forward=Vector3.ProjectOnPlane(head.forward,Vector3.up);
            if(forward.sqrMagnitude>.001f)
                origin.rotation=Quaternion.AngleAxis(Vector3.SignedAngle(forward,Vector3.forward,Vector3.up),Vector3.up)*origin.rotation;
            origin.position+=SeatedEye-head.position;
        }
        public void Configure(Canvas target,OVRCameraRig cameraRig,RectTransform fullPanel,RectTransform compactPanel=null)
        {
            canvas=target;rig=cameraRig;full=fullPanel;compact=compactPanel;
            var rect=(RectTransform)canvas.transform;rect.localScale=Vector3.one*.0011f;
            canvas.renderMode=RenderMode.WorldSpace;canvas.worldCamera=rig.centerEyeAnchor.GetComponent<Camera>();
            var shader=Resources.Load<Shader>("KitchenWorldPanel");
            if(shader==null)throw new System.InvalidOperationException("World panel shader missing");
            overlay=new Material(shader);
            foreach(var graphic in canvas.GetComponentsInChildren<Graphic>(true))graphic.material=overlay;
            Recall();StartCoroutine(PositionTrackedSeat());
        }
        public void Recall(){SetExpanded(true);}
        public void SetExpanded(bool expanded)
        {
            Expanded=expanded;full.gameObject.SetActive(expanded);
            if(compact!=null)compact.gameObject.SetActive(!expanded);
            canvas.enabled=expanded || compact!=null;
            var head=rig.centerEyeAnchor;
            Vector3 forward=Vector3.ProjectOnPlane(head.forward,Vector3.up).normalized;
            if(forward.sqrMagnitude<.001f)forward=Vector3.forward;
            var rotation=Quaternion.LookRotation(forward,Vector3.up);
            Vector3 offset=expanded?new Vector3(0,-.06f,PanelDistance):new Vector3(.32f,-.30f,.85f);
            canvas.transform.SetPositionAndRotation(head.position+rotation*offset,rotation);
        }
        IEnumerator PositionTrackedSeat()
        {
            int stable=0;Vector3 previous=Vector3.positiveInfinity;
            while(true)
            {
                yield return new WaitForEndOfFrame();
                var device=InputDevices.GetDeviceAtXRNode(XRNode.Head);
                bool tracked=device.isValid && device.TryGetFeatureValue(CommonUsages.isTracked,out bool value) && value;
                Vector3 position=rig.centerEyeAnchor.localPosition;
                stable=tracked && (position-previous).sqrMagnitude<.0004f?stable+1:0;previous=position;
                if(stable<3)continue;
                AlignSeatedOrigin(rig.transform,rig.centerEyeAnchor);SetExpanded(Expanded);
                Debug.Log("KITCHEN_VR_SEATED_ORIGIN_READY eye="+rig.centerEyeAnchor.position);
                yield break;
            }
        }
        void Update()
        {
            if(canvas==null)return;
            if(OVRInput.GetDown(OVRInput.Button.Two,OVRInput.Controller.RTouch) ||
                OVRInput.GetDown(OVRInput.Button.Two,OVRInput.Controller.LTouch))SetExpanded(!Expanded);
        }
        void OnDestroy(){if(overlay!=null)Destroy(overlay);}
    }
}
