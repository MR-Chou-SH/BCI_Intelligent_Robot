using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace BCIIntelligentRobot.Kitchen
{
    // Visual skin only. Every operational object's parent is a MuJoCo body.
    public sealed class KitchenPlatformArt : MonoBehaviour
    {
        public KitchenSceneMirror Mirror;
        bool applied;
        readonly List<UnityEngine.Object> owned = new List<UnityEngine.Object>();
        readonly Dictionary<PrimitiveType, Mesh> primitives = new Dictionary<PrimitiveType, Mesh>();
        readonly List<MeshRenderer> generated = new List<MeshRenderer>();
        Material steel, ceramic, wood, rubber, green, stem, bread, meat, cheese, red;
        Material enamel, trim, fabric, worktop, glass, cavity, gasket;
        Shader shader;

        void Start() { Apply(); }
        Material Mat(string name, Color color, float metallic=0, float smooth=.3f)
        {
            var value = new Material(shader) { name = name, color = color };
            value.SetFloat("_Metallic",metallic); value.SetFloat("_Glossiness",smooth);
            if(value.HasProperty("_Smoothness")) value.SetFloat("_Smoothness",smooth);
            owned.Add(value); return value;
        }
        static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
        static bool Finite(Vector3 value) => Finite(value.x) && Finite(value.y) && Finite(value.z);
        void CheckMesh(Mesh mesh)
        {
            foreach(var vertex in mesh.vertices)
                if(!Finite(vertex)) throw new InvalidOperationException("Nonfinite art vertex: "+mesh.name);
            if(!Finite(mesh.bounds.center) || !Finite(mesh.bounds.size))
                throw new InvalidOperationException("Nonfinite art bounds: "+mesh.name);
        }
        Mesh MakeMesh(string name,List<Vector3> vertices,List<Vector2> uv,List<int> triangles,List<Vector3> normals=null)
        {
            foreach(var vertex in vertices)
                if(!Finite(vertex)) throw new InvalidOperationException("Nonfinite art vertex: "+name);
            foreach(int index in triangles)
                if(index<0 || index>=vertices.Count) throw new InvalidOperationException("Art index out of range: "+name);
            var mesh=new Mesh { name=name, indexFormat=vertices.Count>65535?IndexFormat.UInt32:IndexFormat.UInt16 };
            mesh.SetVertices(vertices); mesh.SetUVs(0,uv); mesh.SetTriangles(triangles,0);
            if(normals==null) mesh.RecalculateNormals(); else mesh.SetNormals(normals);
            mesh.RecalculateBounds(); mesh.RecalculateTangents(); CheckMesh(mesh); owned.Add(mesh);
            return mesh;
        }
        GameObject MeshObject(Transform parent,string name,Mesh mesh,Material material)
        {
            var item=new GameObject(name); item.transform.SetParent(parent,false);
            item.AddComponent<MeshFilter>().sharedMesh=mesh;
            var renderer=item.AddComponent<MeshRenderer>(); renderer.sharedMaterial=material;
            if(material.renderQueue>=(int)RenderQueue.Transparent)
            { renderer.shadowCastingMode=ShadowCastingMode.Off; renderer.receiveShadows=false; }
            generated.Add(renderer); return item;
        }
        Mesh BevelMesh(string name,Vector3 size,float bevel)
        {
            Vector3 half=size*.5f;
            bevel=Mathf.Clamp(bevel,0,Mathf.Min(half.x,Mathf.Min(half.y,half.z))*.8f);
            Vector3 inner=half-Vector3.one*bevel;
            var vertices=new List<Vector3>(); var uv=new List<Vector2>();
            var normals=new List<Vector3>(); var triangles=new List<int>();
            foreach(var n in new[]{Vector3.right,Vector3.left,Vector3.up,Vector3.down,Vector3.forward,Vector3.back})
            {
                Vector3 u=Mathf.Abs(n.y)>.5f?Vector3.right:Vector3.up, v=Vector3.Cross(n,u);
                float hu=Vector3.Dot(half,new Vector3(Mathf.Abs(u.x),Mathf.Abs(u.y),Mathf.Abs(u.z)));
                float hv=Vector3.Dot(half,new Vector3(Mathf.Abs(v.x),Mathf.Abs(v.y),Mathf.Abs(v.z)));
                float hn=Mathf.Abs(Vector3.Dot(n,half));
                float[] xs={-hu,-hu+bevel*.3f,-hu+bevel,hu-bevel,hu-bevel*.3f,hu};
                float[] ys={-hv,-hv+bevel*.3f,-hv+bevel,hv-bevel,hv-bevel*.3f,hv};
                int start=vertices.Count;
                for(int y=0;y<6;y++) for(int x=0;x<6;x++)
                {
                    Vector3 p=n*hn+u*xs[x]+v*ys[y];
                    Vector3 q=new Vector3(Mathf.Clamp(p.x,-inner.x,inner.x),Mathf.Clamp(p.y,-inner.y,inner.y),Mathf.Clamp(p.z,-inner.z,inner.z));
                    Vector3 normal=bevel>0?(p-q).normalized:n;
                    vertices.Add(bevel>0?q+normal*bevel:p); normals.Add(normal);
                    uv.Add(new Vector2(xs[x]+hu,ys[y]+hv));
                    if(x<5 && y<5)
                    {
                        int a=start+y*6+x;
                        triangles.AddRange(new[]{a,a+1,a+7,a,a+7,a+6});
                    }
                }
            }
            return MakeMesh(name,vertices,uv,triangles,normals);
        }
        GameObject Box(Transform parent,string name,Vector3 position,Vector3 size,Material material,float bevel=.002f)
        {
            var item=MeshObject(parent,name,BevelMesh(name,size,bevel),material);
            item.transform.localPosition=position; return item;
        }
        Mesh PrimitiveMesh(PrimitiveType kind)
        {
            if(primitives.TryGetValue(kind,out Mesh cached)) return cached;
            if(kind==PrimitiveType.Cube) cached=BevelMesh("art_unit_beveled_box",Vector3.one,.035f);
            else
            {
                const int segments=24;
                int rows=kind==PrimitiveType.Sphere?16:3;
                var vertices=new List<Vector3>(); var uv=new List<Vector2>(); var triangles=new List<int>();
                for(int row=0;row<=rows;row++) for(int j=0;j<=segments;j++)
                {
                    float t=(float)row/rows,angle=2*Mathf.PI*j/segments;
                    float radius=kind==PrimitiveType.Sphere?.5f*Mathf.Max(0,Mathf.Sin(Mathf.PI*t)):(row==0 || row==rows?0:.5f);
                    float y=kind==PrimitiveType.Sphere?-.5f*Mathf.Cos(Mathf.PI*t):(row<2?-1:1);
                    vertices.Add(new Vector3(radius*Mathf.Cos(angle),y,radius*Mathf.Sin(angle)));
                    uv.Add(new Vector2((float)j/segments,t));
                    if(row>0 && j>0)
                    { int k=row*(segments+1)+j; triangles.AddRange(new[]{k,k-segments-2,k-1,k,k-segments-1,k-segments-2}); }
                }
                cached=MakeMesh("art_unit_"+kind,vertices,uv,triangles);
            }
            primitives.Add(kind,cached); return cached;
        }
        GameObject Shape(PrimitiveType kind, Transform parent, string name, Vector3 position, Vector3 scale, Material material)
        {
            var item=MeshObject(parent,name,PrimitiveMesh(kind),material);
            item.transform.localPosition=position; item.transform.localScale=scale;
            return item;
        }
        void Tube(Transform parent, string name, Vector3 a, Vector3 b, float radius, Material material)
        {
            var tube=Shape(PrimitiveType.Cylinder,parent,name,(a+b)*.5f,new Vector3(radius*2,(b-a).magnitude*.5f,radius*2),material);
            tube.transform.localRotation=Quaternion.FromToRotation(Vector3.up,b-a);
        }
        void Sweep(Transform parent,string name,IList<Vector3> path,float radius,Material material)
        {
            const int sides=12;
            var vertices=new List<Vector3>(); var uv=new List<Vector2>(); var triangles=new List<int>();
            Vector3 normal=Vector3.zero,previous=Vector3.up;
            for(int i=0;i<path.Count;i++)
            {
                Vector3 tangent=(path[Mathf.Min(i+1,path.Count-1)]-path[Mathf.Max(0,i-1)]).normalized;
                if(i==0) normal=Vector3.Cross(tangent,Mathf.Abs(tangent.y)>.9f?Vector3.forward:Vector3.up).normalized;
                else normal=(Quaternion.FromToRotation(previous,tangent)*normal).normalized;
                Vector3 binormal=Vector3.Cross(tangent,normal); previous=tangent;
                for(int j=0;j<=sides;j++)
                {
                    float a=2*Mathf.PI*j/sides;
                    vertices.Add(path[i]+radius*(Mathf.Cos(a)*normal+Mathf.Sin(a)*binormal));
                    uv.Add(new Vector2((float)j/sides,(float)i/(path.Count-1)));
                    if(i>0 && j>0)
                    { int k=i*(sides+1)+j; triangles.AddRange(new[]{k-sides-2,k-sides-1,k,k-sides-2,k,k-1}); }
                }
            }
            if((path[0]-path[path.Count-1]).sqrMagnitude>1e-10f)
            {
                int first=vertices.Count; vertices.Add(path[0]); uv.Add(Vector2.zero);
                int last=vertices.Count; vertices.Add(path[path.Count-1]); uv.Add(Vector2.one);
                for(int j=0;j<sides;j++)
                    triangles.AddRange(new[]{first,j+1,j,last,(path.Count-1)*(sides+1)+j,(path.Count-1)*(sides+1)+j+1});
            }
            MeshObject(parent,name,MakeMesh(name,vertices,uv,triangles),material);
        }
        void Ring(Transform parent,string name,Vector3 center,Vector3 axis,float radius,float radial,float axial,Material material)
        {
            const int segments=48,sides=10;
            var vertices=new List<Vector3>(); var uv=new List<Vector2>(); var triangles=new List<int>();
            for(int i=0;i<=segments;i++) for(int j=0;j<=sides;j++)
            {
                float a=2*Mathf.PI*i/segments,b=2*Mathf.PI*j/sides,r=radius+radial*Mathf.Cos(b);
                vertices.Add(new Vector3(r*Mathf.Cos(a),axial*Mathf.Sin(b),r*Mathf.Sin(a)));
                uv.Add(new Vector2((float)i/segments,(float)j/sides));
                if(i<segments && j<sides)
                { int k=i*(sides+1)+j; triangles.AddRange(new[]{k,k+1,k+sides+2,k,k+sides+2,k+sides+1}); }
            }
            var item=MeshObject(parent,name,MakeMesh(name,vertices,uv,triangles),material);
            item.transform.localPosition=center; item.transform.localRotation=Quaternion.FromToRotation(Vector3.up,axis);
        }
        List<Vector3> RoundedLoop(float halfX,float halfZ,float radius,float y)
        {
            radius=Mathf.Min(radius,Mathf.Min(halfX,halfZ));
            var points=new List<Vector3>();
            for(int corner=0;corner<4;corner++) for(int j=0;j<=6;j++)
            {
                float a=(corner+j/6f)*Mathf.PI*.5f;
                float cx=(corner==0 || corner==3?1:-1)*(halfX-radius),cz=(corner<2?1:-1)*(halfZ-radius);
                points.Add(new Vector3(cx+radius*Mathf.Cos(a),y,cz+radius*Mathf.Sin(a)));
            }
            points.Add(points[0]); return points;
        }
        Transform Geom(string name)
        {
            foreach(var geom in Mirror.Manifest.geoms) if(geom.name==name)
                foreach(var body in Mirror.Manifest.bodies) if(body.id==geom.body_id)
                    return Mirror.transform.Find(body.name+"/"+name);
            return null;
        }
        KitchenGeom Spec(string name)
        {
            foreach(var geom in Mirror.Manifest.geoms) if(geom.name==name) return geom;
            throw new InvalidOperationException("Visual geometry missing: "+name);
        }
        Transform VisualRoot(Transform body,string name)
        {
            var item=new GameObject(name); item.transform.SetParent(body,false); return item.transform;
        }
        Transform DetailFrame(Transform source,string name,bool hide=true)
        {
            if(hide) foreach(var renderer in source.GetComponentsInChildren<Renderer>()) renderer.enabled=false;
            var item=new GameObject(name); item.transform.SetParent(source.parent,false);
            item.transform.localPosition=source.localPosition; item.transform.localRotation=source.localRotation;
            // Sibling of the scaled proxy: all detail dimensions remain metres in the true body frame.
            return item.transform;
        }
        void FaceFrame(Transform parent,string name,Vector3 center,Vector3 size,float border,Material material)
        {
            foreach(float sign in new[]{-1f,1f})
            {
                Box(parent,name+"_horizontal",center+Vector3.up*(sign*(size.y-border)*.5f),new Vector3(size.x,border,size.z),material);
                Box(parent,name+"_vertical",center+Vector3.forward*(sign*(size.z-border)*.5f),new Vector3(size.x,size.y-2*border,border),material);
            }
        }
        void BatchDetails()
        {
            var groups=new Dictionary<Transform,Dictionary<Material,List<MeshRenderer>>>();
            foreach(var renderer in generated)
            {
                Transform body=renderer.transform;
                while(body.parent!=Mirror.transform && body.parent!=null) body=body.parent;
                if(body.parent!=Mirror.transform) throw new InvalidOperationException("Art has no synchronized body parent");
                if(!groups.TryGetValue(body,out var materials)) groups[body]=materials=new Dictionary<Material,List<MeshRenderer>>();
                if(!materials.TryGetValue(renderer.sharedMaterial,out var items)) materials[renderer.sharedMaterial]=items=new List<MeshRenderer>();
                items.Add(renderer);
            }
            foreach(var body in groups) foreach(var group in body.Value)
            {
                if(group.Value.Count<2) continue;
                var instances=new List<CombineInstance>(); int count=0;
                foreach(var renderer in group.Value)
                {
                    Mesh mesh=renderer.GetComponent<MeshFilter>().sharedMesh; count+=mesh.vertexCount;
                    instances.Add(new CombineInstance { mesh=mesh,transform=body.Key.worldToLocalMatrix*renderer.localToWorldMatrix });
                }
                var combined=new Mesh { name="art_"+body.Key.name+"_"+group.Key.name,indexFormat=count>65535?IndexFormat.UInt32:IndexFormat.UInt16 };
                combined.CombineMeshes(instances.ToArray(),true,true); combined.RecalculateBounds(); CheckMesh(combined); owned.Add(combined);
                MeshObject(body.Key,combined.name,combined,group.Key);
                foreach(var renderer in group.Value) renderer.enabled=false;
            }
        }
        GameObject Surface(Transform parent,string name,Vector2[] profile,Vector3 scale,Material material,int lobes=0,float amplitude=0)
        {
            const int segments=64;
            var smoothProfile=new List<Vector2>();
            for(int row=0;row<profile.Length-1;row++) for(int sample=0;sample<4;sample++)
            {
                Vector2 a=profile[Mathf.Max(0,row-1)],b=profile[row],c=profile[row+1],d=profile[Mathf.Min(profile.Length-1,row+2)];
                float t=sample/4f;
                Vector2 p=.5f*((2*b)+(-a+c)*t+(2*a-5*b+4*c-d)*t*t+(-a+3*b-3*c+d)*t*t*t);
                p.x=Mathf.Max(0,p.x);smoothProfile.Add(p);
            }
            smoothProfile.Add(profile[profile.Length-1]);profile=smoothProfile.ToArray();
            var vertices=new List<Vector3>(); var uv=new List<Vector2>(); var triangles=new List<int>();
            for(int row=0;row<profile.Length;row++) for(int j=0;j<=segments;j++)
            {
                float angle=2*Mathf.PI*j/segments;
                float radius=profile[row].x*(1+amplitude*Mathf.Cos(lobes*angle));
                vertices.Add(Vector3.Scale(new Vector3(radius*Mathf.Cos(angle),profile[row].y,radius*Mathf.Sin(angle)),scale));
                uv.Add(new Vector2((float)j/segments,(float)row/(profile.Length-1)));
                if(row>0 && j>0) { int k=row*(segments+1)+j; triangles.AddRange(new[]{k,k-segments-2,k-1,k,k-segments-1,k-segments-2}); }
            }
            return MeshObject(parent,name,MakeMesh(name,vertices,uv,triangles),material);
        }
        void CurvedBanana(Transform parent,Material material)
        {
            const int rings=40,segments=32;
            var vertices=new List<Vector3>();var uv=new List<Vector2>();var triangles=new List<int>();
            for(int i=0;i<=rings;i++)
            {
                float t=(float)i/rings,s=2*t-1;
                Vector3 center=new Vector3(.075f*s,.005f*(1-s*s),-.015f*s*s);
                Vector3 tangent=new Vector3(.15f,-.02f*s,-.06f*s).normalized;
                Vector3 binormal=Vector3.Cross(tangent,Vector3.up).normalized,normal=Vector3.Cross(binormal,tangent).normalized;
                float radius=.0035f+.0125f*Mathf.Pow(Mathf.Max(0,Mathf.Sin(Mathf.PI*t)),.4f);
                for(int j=0;j<=segments;j++)
                {
                    float angle=2*Mathf.PI*j/segments;
                    vertices.Add(center+radius*(Mathf.Cos(angle)*binormal+Mathf.Sin(angle)*normal));uv.Add(new Vector2(t,(float)j/segments));
                    if(i<rings && j<segments) {int a=i*(segments+1)+j,b=a+segments+1;triangles.AddRange(new[]{a,b,b+1,a,b+1,a+1});}
                }
            }
            MeshObject(parent,"banana_continuous_curved_peel",MakeMesh("banana_continuous_curved_peel",vertices,uv,triangles),material);
            Tube(parent,"stem",new Vector3(-.075f,0,-.015f),new Vector3(-.084f,.003f,-.017f),.0038f,stem);
        }
        Transform Skin(string body)
        {
            var parent=Mirror.transform.Find(body);
            if(parent==null) throw new InvalidOperationException("Visual body missing: "+body);
            foreach(var renderer in parent.GetComponentsInChildren<Renderer>()) renderer.enabled=false;
            var node=new GameObject("VisualSkin_"+body); node.transform.SetParent(parent,false); return node.transform;
        }
        void Fruit(string name,Vector2[] profile,Vector3 scale,Color color,int lobes,float amplitude)
        {
            var skin=Skin(name);
            var material=Mat(name+"_skin",color,0,.38f);
            Surface(skin,name+"_contoured_skin",profile,scale,material,lobes,amplitude);
            float top=scale.y*profile[profile.Length-1].y;
            Tube(skin,"stem",new Vector3(0,top-.001f,0),new Vector3(.003f,top+.007f,.001f),.0015f,stem);
            var leaf=Shape(PrimitiveType.Sphere,skin,"leaf",new Vector3(.008f,top+.003f,0),new Vector3(.017f,.001f,.006f),green);
            leaf.transform.localRotation=Quaternion.Euler(0,20,10);
        }
        void Foods()
        {
            Fruit("apple",new[]{new Vector2(0,-.91f),new Vector2(.5f,-.85f),new Vector2(.91f,-.4f),new Vector2(1,.15f),new Vector2(.86f,.66f),new Vector2(.48f,.88f),new Vector2(0,.79f)},new Vector3(.034f,.035f,.034f),new Color(.63f,.035f,.02f),5,.035f);
            Fruit("pear",new[]{new Vector2(0,-.95f),new Vector2(.66f,-.85f),new Vector2(1,-.4f),new Vector2(.88f,.05f),new Vector2(.49f,.53f),new Vector2(.25f,.91f),new Vector2(0,.96f)},new Vector3(.031f,.044f,.031f),new Color(.57f,.66f,.13f),5,.012f);
            Fruit("tomato",new[]{new Vector2(0,-.9f),new Vector2(.74f,-.7f),new Vector2(1,0),new Vector2(.8f,.65f),new Vector2(0,.86f)},new Vector3(.031f,.03f,.031f),new Color(.82f,.055f,.025f),6,.035f);
            var banana=Skin("banana");
            var yellow=Mat("banana_peel",new Color(.93f,.73f,.08f),0,.25f);
            CurvedBanana(banana,yellow);
            Shape(PrimitiveType.Sphere,banana,"banana_dried_tip",new Vector3(.075f,0,-.015f),new Vector3(.007f,.007f,.007f),stem);
            var tomato=Mirror.transform.Find("tomato/VisualSkin_tomato");
            for(int i=0;i<5;i++)
            {
                float a=i*2*Mathf.PI/5;
                var leaf=Shape(PrimitiveType.Sphere,tomato,"tomato_calyx",new Vector3(.006f*Mathf.Cos(a),.026f,.006f*Mathf.Sin(a)),new Vector3(.018f,.0012f,.0035f),green);
                leaf.transform.localRotation=Quaternion.Euler(0,-a*Mathf.Rad2Deg,0);
            }
            var burger=Skin("burger");
            bool flatBun=Spec("burger_food").type==7;
            var lower=Surface(burger,"lower_bun",flatBun?
                new[]{new Vector2(0,-.028f),new Vector2(.027f,-.028f),new Vector2(.037f,-.028f),new Vector2(.043f,-.024f),new Vector2(.044f,-.018f),new Vector2(.044f,-.009f),new Vector2(0,-.008f)}:
                new[]{new Vector2(0,-.027f),new Vector2(.029f,-.026f),new Vector2(.044f,-.018f),new Vector2(.044f,-.009f),new Vector2(0,-.008f)},Vector3.one,bread);
            if(flatBun)
            {
                var mesh=lower.GetComponent<MeshFilter>().sharedMesh;var points=mesh.vertices;
                for(int i=0;i<points.Length;i++)points[i].y=Mathf.Max(points[i].y,-.028f);
                mesh.vertices=points;mesh.RecalculateNormals();mesh.RecalculateBounds();
            }
            Surface(burger,"patty",new[]{new Vector2(0,-.01f),new Vector2(.043f,-.009f),new Vector2(.044f,0),new Vector2(0,.001f)},Vector3.one,meat,15,.016f);
            Shape(PrimitiveType.Cube,burger,"cheese",new Vector3(0,.001f,0),new Vector3(.068f,.002f,.068f),cheese).transform.localRotation=Quaternion.Euler(0,12,0);
            Surface(burger,"lettuce",new[]{new Vector2(0,.002f),new Vector2(.045f,.003f),new Vector2(.045f,.006f),new Vector2(0,.007f)},Vector3.one,green,12,.035f);
            Surface(burger,"upper_bun",new[]{new Vector2(0,.006f),new Vector2(.045f,.007f),new Vector2(.044f,.013f),new Vector2(.035f,.023f),new Vector2(.018f,.028f),new Vector2(0,.028f)},Vector3.one,bread);
            var sesame=Mat("sesame",new Color(.92f,.86f,.57f),0,.2f);
            for(int i=0;i<28;i++) { float a=i*2.39996f; float r=.032f*Mathf.Sqrt((i+.5f)/28); float y=.028f-.012f*r*r/(.032f*.032f); Shape(PrimitiveType.Sphere,burger,"sesame_"+i,new Vector3(r*Mathf.Cos(a),y,r*Mathf.Sin(a)),new Vector3(.003f,.001f,.0015f),sesame); }
            if(flatBun)FitFoodSkin(burger,Geom("burger_food"));
            var dog=Skin("hotdog");
            foreach(float z in new[]{-.012f,.012f}) Shape(PrimitiveType.Sphere,dog,"split_bun",new Vector3(0,-.003f,z),new Vector3(.118f,.033f,.024f),bread);
            Shape(PrimitiveType.Sphere,dog,"sausage",new Vector3(0,.006f,0),new Vector3(.111f,.024f,.022f),red);
            for(int i=0;i<12;i++) { float x=-.047f+i*.0085f; Tube(dog,"mustard_"+i,new Vector3(x,.017f,.004f*Mathf.Sin(i*1.8f)),new Vector3(x+.0085f,.017f,.004f*Mathf.Sin((i+1)*1.8f)),.0013f,cheese); }
        }
        void FitFoodSkin(Transform skin,Transform source)
        {
            // Fit only the owned skin to the compiled hull in the unchanged food-body frame.
            Bounds native=new Bounds(),visual=new Bounds();bool first=true;
            foreach(var vertex in source.GetComponent<MeshFilter>().sharedMesh.vertices)
            {
                Vector3 point=skin.parent.InverseTransformPoint(source.TransformPoint(vertex));
                if(first){native=new Bounds(point,Vector3.zero);first=false;}else native.Encapsulate(point);
            }
            first=true;
            foreach(var filter in skin.GetComponentsInChildren<MeshFilter>())foreach(var vertex in filter.sharedMesh.vertices)
            {
                Vector3 point=skin.InverseTransformPoint(filter.transform.TransformPoint(vertex));
                if(first){visual=new Bounds(point,Vector3.zero);first=false;}else visual.Encapsulate(point);
            }
            if(native.size.x<=0 || native.size.y<=0 || native.size.z<=0 || visual.size.x<=0 || visual.size.y<=0 || visual.size.z<=0)
                throw new InvalidOperationException("Food skin or compiled hull has invalid dimensions");
            var scale=new Vector3(native.size.x/visual.size.x,native.size.y/visual.size.y,native.size.z/visual.size.z);
            skin.localScale=scale;skin.localPosition=native.center-Vector3.Scale(visual.center,scale);
            Debug.Log("KITCHEN_PLATFORM_VISUAL_FOOD_FIT nativeSizeMeters="+native.size.ToString("F6")+" visualScale="+scale.ToString("F6"));
        }
        void Microwave()
        {
            string[] parts={"microwave_bottom","microwave_top","microwave_back","microwave_side_front","microwave_side_back",
                "microwave_button_pocket_lower","microwave_button_pocket_upper"};
            for(int i=0;i<parts.Length;i++)
            {
                var source=Geom(parts[i]); if(source==null) continue;
                var detail=DetailFrame(source,"VisualSkin_"+parts[i]); Vector3 size=source.localScale;
                Box(detail,"formed_appliance_shell",Vector3.zero,size,enamel,.003f);
                int axis=i<2?1:i==2?0:2; float sign=i==0 || i==3 || i>=5?1:-1;
                Vector3 lining=size,position=Vector3.zero;
                lining[axis]=.0004f; position[axis]=sign*(size[axis]*.5f+.0001f);
                Box(detail,"cavity_enamel_lining",position,lining,cavity,.0001f);
                if(i==2)
                    for(int row=0;row<8;row++)
                        Box(detail,"rear_vent_slot",new Vector3(-size.x*.5f-.0004f,-.07f+row*.015f,0),new Vector3(.0004f,.002f,Mathf.Min(.105f,size.z*.55f)),trim,.0001f);
                if(i==3 || i==4)
                    for(int row=0;row<3;row++)
                        Box(detail,"pressed_cavity_wall_seam",position+Vector3.up*((row-1)*size.y*.22f),
                            new Vector3(size.x*.72f,.0008f,.0005f),steel,.0001f);
            }
            var bottom=Geom("microwave_bottom"); var top=Geom("microwave_top");
            if(bottom!=null && top!=null)
            {
                var bezel=DetailFrame(bottom,"VisualSkin_fixed_cavity_perimeter",false);
                bezel.localPosition=new Vector3(bottom.localPosition.x-bottom.localScale.x*.5f+.001f,
                    (bottom.localPosition.y-bottom.localScale.y*.5f+top.localPosition.y+top.localScale.y*.5f)*.5f,bottom.localPosition.z);
                float height=top.localPosition.y+top.localScale.y*.5f-bottom.localPosition.y+bottom.localScale.y*.5f;
                // Four perimeter rails only: no static door or front plane across the cavity.
                FaceFrame(bezel,"cavity_perimeter",Vector3.zero,new Vector3(.004f,height,bottom.localScale.z),.024f,steel);
                FaceFrame(bezel,"cavity_seal",Vector3.right*.0022f,
                    new Vector3(.0006f,height-.012f,bottom.localScale.z-.012f),.003f,gasket);
                var floorDetail=DetailFrame(bottom,"VisualSkin_cavity_floor_detail",false);
                Ring(floorDetail,"stamped_floor_outline",new Vector3(.025f,bottom.localScale.y*.5f+.0002f,0),
                    Vector3.up,Mathf.Min(bottom.localScale.x,bottom.localScale.z)*.30f,.0007f,.0002f,steel);
            }
            var panel=Geom("microwave_door_panel");
            if(panel!=null)
            {
                var door=DetailFrame(panel,"VisualSkin_moving_microwave_door"); Vector3 size=panel.localScale;
                float border=Mathf.Min(size.y,size.z)*.08f;
                FaceFrame(door,"beveled_door_frame",Vector3.zero,size,border,trim);
                FaceFrame(door,"inner_door_seal",Vector3.right*(size.x*.5f-.0004f),
                    new Vector3(.0008f,size.y-.004f,size.z-.004f),border*.45f,gasket);
                FaceFrame(door,"brushed_window_surround",Vector3.left*(size.x*.5f+.0004f),
                    new Vector3(.0012f,size.y-2*border+.004f,size.z-2*border+.004f),.0018f,steel);
                Vector3 window=new Vector3(.0016f,size.y-2*border,size.z-2*border);
                Box(door,"transparent_observation_glass",Vector3.zero,window,glass,.0004f);
                // Low-frequency visual screen; not an electromagnetic safety model.
                const int screenDivisions=16;
                var screen=Mat("matte_observation_screen",new Color(.14f,.16f,.17f),0,.12f);
                for(int i=1;i<screenDivisions;i++)
                    Box(door,"screen_vertical_wire",new Vector3(.002f,0,(i/(float)screenDivisions-.5f)*window.z),new Vector3(.0003f,window.y,.0008f),screen,.00008f);
                for(int i=1;i<screenDivisions;i++)
                    Box(door,"screen_horizontal_wire",new Vector3(.002f,(i/(float)screenDivisions-.5f)*window.y,0),new Vector3(.0003f,.0008f,window.z),screen,.00008f);
            }
            var handle=Geom("microwave_door_handle");
            if(handle!=null)
            {
                var detail=DetailFrame(handle,"VisualSkin_moving_door_grip"); Vector3 size=handle.localScale;
                Box(detail,"molded_grip",Vector3.zero,size,trim,.005f);
                foreach(float sign in new[]{-1f,1f})
                    Box(detail,"grip_end_cap",Vector3.up*(sign*(size.y*.5f-.005f)),new Vector3(size.x,.008f,size.z),steel,.002f);
            }
            var side=Geom("microwave_side_front");
            if(side!=null && bottom!=null)
            {
                var panelDetail=DetailFrame(side,"VisualSkin_fixed_control_fascia",false);
                Vector3 d=side.localScale;
                panelDetail.localPosition=new Vector3(bottom.localPosition.x-bottom.localScale.x*.5f-.001f,side.localPosition.y,side.localPosition.z);
                var lower=Geom("microwave_button_pocket_lower");var upper=Geom("microwave_button_pocket_upper");
                if(lower!=null && upper!=null)
                {
                    // Follow the actual split wall; no decorative plane across the button stroke pocket.
                    float cutLow=lower.localPosition.y+lower.localScale.y*.5f-side.localPosition.y;
                    float cutHigh=upper.localPosition.y-upper.localScale.y*.5f-side.localPosition.y;
                    float low=-d.y*.5f+.008f,high=d.y*.5f-.008f;
                    if(cutLow<=low || cutHigh>=high || cutHigh<=cutLow)throw new InvalidOperationException("Invalid exported button pocket bounds");
                    Box(panelDetail,"lower_control_fascia",new Vector3(0,(low+cutLow)*.5f,0),new Vector3(.002f,cutLow-low,d.z-.002f),trim,.001f);
                    Box(panelDetail,"upper_control_fascia",new Vector3(0,(cutHigh+high)*.5f,0),new Vector3(.002f,high-cutHigh,d.z-.002f),trim,.001f);
                    Debug.Log("KITCHEN_PLATFORM_VISUAL_BUTTON_POCKET height_m="+(cutHigh-cutLow).ToString("F6"));
                }
                else Box(panelDetail,"control_fascia",Vector3.zero,new Vector3(.002f,d.y-.016f,d.z-.002f),trim,.001f);
                Box(panelDetail,"unlit_display_inset",new Vector3(-.0012f,.092f,0),new Vector3(.001f,.035f,d.z-.006f),rubber,.0003f);
                // Printed scale marks only; no invented live timer or extra operable controls.
                for(int i=0;i<3;i++)
                    Box(panelDetail,"printed_control_legend",new Vector3(-.0013f,.058f-i*.006f,0),new Vector3(.0004f,.001f,d.z-.009f),steel,.0001f);
                foreach(float sign in new[]{-1f,1f})
                    Box(panelDetail,"fascia_edge_highlight",new Vector3(-.0014f,sign*(d.y*.5f-.012f),0),
                        new Vector3(.0003f,.0007f,Mathf.Max(.001f,d.z-.006f)),steel,.0001f);
            }
            var button=Geom("microwave_start_geom");
            if(button!=null)
            {
                var detail=DetailFrame(button,"VisualSkin_real_moving_start_button"); Vector3 d=button.localScale;
                Box(detail,"pressable_button_face",Vector3.zero,d,steel,.003f);
                float x=-d.x*.5f-.0003f,r=Mathf.Min(d.y,d.z)*.2f;
                var vertices=new List<Vector3>{new Vector3(x,-r,r),new Vector3(x,r,r),new Vector3(x,0,-r)};
                MeshObject(detail,"engraved_start_symbol",MakeMesh("start_glyph",vertices,new List<Vector2>{Vector2.zero,Vector2.up,Vector2.one},new List<int>{0,1,2}),trim);
            }
        }
        void SinkAndBasket()
        {
            var floor=Geom("sink_floor"); var wall=Geom("sink_wall_0"); var end=Geom("sink_wall_2");
            if(floor!=null && wall!=null && end!=null)
            {
                var sink=VisualRoot(floor.parent,"VisualSkin_formed_sink_bowl");
                float outerX=Mathf.Abs(wall.localPosition.x)+wall.localScale.x*.5f;
                float outerZ=Mathf.Abs(end.localPosition.z)+end.localScale.z*.5f;
                float innerX=outerX-wall.localScale.x,innerZ=outerZ-end.localScale.z;
                float top=wall.localPosition.y+wall.localScale.y*.5f,baseY=floor.localPosition.y+floor.localScale.y*.5f;
                var rings=new[]{RoundedLoop(outerX,outerZ,.014f,top),RoundedLoop(innerX,innerZ,.012f,top+.0003f),
                    RoundedLoop(innerX,innerZ,.018f,baseY+.015f),RoundedLoop(innerX-.012f,innerZ-.012f,.021f,baseY+.0003f)};
                var vertices=new List<Vector3>(); var uv=new List<Vector2>(); var triangles=new List<int>();
                int count=rings[0].Count;
                for(int row=0;row<rings.Length;row++) for(int j=0;j<count;j++)
                {
                    vertices.Add(rings[row][j]); uv.Add(new Vector2((float)j/(count-1),row/3f));
                    if(row>0 && j>0)
                    { int a=(row-1)*count+j-1,b=row*count+j-1; triangles.AddRange(new[]{a,b,b+1,a,b+1,a+1}); }
                }
                MeshObject(sink,"continuous_drawn_stainless_bowl",MakeMesh("sink_drawn_bowl",vertices,uv,triangles),steel);
                Sweep(sink,"sink_rolled_lip",RoundedLoop(outerX-.0015f,outerZ-.0015f,.014f,top+.0002f),.0012f,steel);
                for(int i=0;i<4;i++)
                {
                    var source=Geom("sink_wall_"+i);
                    if(source!=null) foreach(var renderer in source.GetComponentsInChildren<Renderer>()) renderer.enabled=false;
                }
                Ring(sink,"drain_flange",new Vector3(0,baseY+.001f,0),Vector3.up,.023f,.003f,.0008f,steel);
                Shape(PrimitiveType.Cylinder,sink,"drain_recess",new Vector3(0,baseY+.0008f,0),new Vector3(.039f,.0005f,.039f),trim);
                for(int i=0;i<4;i++)
                {
                    float x=(i-1.5f)*.007f;
                    Tube(sink,"drain_strainer_bar",new Vector3(x,baseY+.0015f,-.014f),new Vector3(x,baseY+.0015f,.014f),.0008f,steel);
                }
            }
            var bottom=Geom("basket_bottom"); var side=Geom("basket_wall_0"); var back=Geom("basket_wall_2");
            if(bottom==null || side==null || back==null) return;
            var basket=DetailFrame(bottom,"VisualSkin_wire_fruit_basket"); Vector3 d=bottom.localScale;
            Box(basket,"pressed_basket_base",Vector3.zero,d,steel,.003f);
            for(int i=-4;i<=4;i++)
                Box(basket,"pressed_drainage_channel",new Vector3(i*d.x/11,d.y*.5f+.0002f,0),
                    new Vector3(.001f,.0003f,d.z*.82f),trim,.0001f);
            float height=side.localPosition.y+side.localScale.y*.5f-bottom.localPosition.y;
            float hx=Mathf.Abs(side.localPosition.x)+side.localScale.x*.5f-.0025f;
            float hz=Mathf.Abs(back.localPosition.z)+back.localScale.z*.5f-.0025f;
            for(int i=0;i<4;i++)
            {
                var source=Geom("basket_wall_"+i);
                if(source!=null) foreach(var renderer in source.GetComponentsInChildren<Renderer>()) renderer.enabled=false;
            }
            Sweep(basket,"rolled_top_wire",RoundedLoop(hx,hz,.008f,height-.0025f),.0025f,steel);
            for(int level=0;level<3;level++)
                Sweep(basket,"horizontal_basket_weave",RoundedLoop(hx-.002f,hz-.002f,.008f,.017f+level*(height-.024f)/3),.0013f,steel);
            for(int i=-5;i<=5;i++) foreach(float sign in new[]{-1f,1f})
            {
                float t=i/6f;
                Tube(basket,"basket_wire_x",new Vector3(t*(hx-.008f),d.y*.5f,sign*(hz-.003f)),new Vector3(t*(hx-.008f),height-.003f,sign*(hz-.003f)),.0014f,steel);
                Tube(basket,"basket_wire_z",new Vector3(sign*(hx-.003f),d.y*.5f,t*(hz-.008f)),new Vector3(sign*(hx-.003f),height-.003f,t*(hz-.008f)),.0014f,steel);
            }
        }
        void Faucet()
        {
            var riser=Geom("faucet_stem"); var spout=Geom("faucet_spout"); var lever=Geom("faucet_handle_geom");
            if(riser==null || spout==null) return;
            var faucet=VisualRoot(riser.parent,"VisualSkin_fixed_faucet");
            float half=Spec("faucet_stem").native_size[1],radius=Spec("faucet_stem").native_size[0];
            Vector3 a=faucet.InverseTransformPoint(riser.TransformPoint(Vector3.up*half)),b=faucet.InverseTransformPoint(riser.TransformPoint(Vector3.down*half));
            Vector3 low=a.y<b.y?a:b,join=a.y>b.y?a:b;
            half=Spec("faucet_spout").native_size[1];
            a=faucet.InverseTransformPoint(spout.TransformPoint(Vector3.up*half)); b=faucet.InverseTransformPoint(spout.TransformPoint(Vector3.down*half));
            Vector3 outlet=(a-join).sqrMagnitude>(b-join).sqrMagnitude?a:b,forward=(outlet-join).normalized;
            float bend=Mathf.Min(.018f,(outlet-join).magnitude*.2f);
            var path=new List<Vector3>{low,join-Vector3.up*bend};
            for(int i=1;i<=8;i++)
            {
                float angle=i*Mathf.PI/16;
                path.Add(join-Vector3.up*bend+forward*bend-forward*(bend*Mathf.Cos(angle))+Vector3.up*(bend*Mathf.Sin(angle)));
            }
            path.Add(outlet);
            Sweep(faucet,"continuous_bent_spout",path,radius,steel);
            foreach(var source in new[]{riser,spout}) foreach(var renderer in source.GetComponentsInChildren<Renderer>()) renderer.enabled=false;
            Shape(PrimitiveType.Cylinder,faucet,"deck_escutcheon",low+Vector3.up*.004f,new Vector3(.046f,.004f,.046f),steel);
            Ring(faucet,"aerator_collar",outlet+Vector3.down*.006f,Vector3.up,.011f,.002f,.003f,steel);
            Shape(PrimitiveType.Cylinder,faucet,"aerator_screen",outlet+Vector3.down*.009f,new Vector3(.019f,.0005f,.019f),trim);
            if(lever==null) return;
            Vector3 pivot=faucet.InverseTransformPoint(lever.parent.position);
            Tube(faucet,"fixed_rotary_valve_body",new Vector3(low.x,pivot.y,low.z),pivot,radius,steel);
            Shape(PrimitiveType.Cylinder,faucet,"valve_spindle_housing",pivot-Vector3.up*.005f,new Vector3(.032f,.012f,.032f),steel);
            var handle=DetailFrame(lever,"VisualSkin_real_rotating_faucet_lever");
            half=Spec("faucet_handle_geom").native_size[1]; radius=Spec("faucet_handle_geom").native_size[0];
            Tube(handle,"lever_grip",Vector3.down*half,Vector3.up*half,radius,steel);
            foreach(float sign in new[]{-1f,1f})
                Shape(PrimitiveType.Sphere,handle,"lever_rounded_end",Vector3.up*(sign*half),Vector3.one*(radius*2),steel);
            Ring(handle,"lever_grip_band",Vector3.up*(half*.65f),Vector3.up,radius,.0005f,.0015f,trim);
        }
        void Wheel(Transform source,float radius,float halfWidth,bool pushRim)
        {
            var wheel=DetailFrame(source,"VisualSkin_"+source.name);
            float tread=Mathf.Min(.018f,radius*.24f),hub=Mathf.Min(.024f,radius*.25f);
            Ring(wheel,"rounded_rubber_tire",Vector3.zero,Vector3.up,radius-tread,tread,halfWidth,rubber);
            Ring(wheel,"alloy_wheel_rim",Vector3.zero,Vector3.up,radius-2*tread,.005f,halfWidth*.65f,steel);
            Shape(PrimitiveType.Cylinder,wheel,"axle_hub",Vector3.zero,new Vector3(hub*2,halfWidth*.85f,hub*2),steel);
            int spokes=pushRim?24:8;
            for(int i=0;i<spokes;i++)
            {
                float a=2*Mathf.PI*i/spokes,b=a+.06f;
                Tube(wheel,"tension_spoke",new Vector3(hub*Mathf.Cos(b),(i%2==0?1:-1)*halfWidth*.6f,hub*Mathf.Sin(b)),
                    new Vector3((radius-2*tread)*Mathf.Cos(a),0,(radius-2*tread)*Mathf.Sin(a)),pushRim?.0013f:.0018f,steel);
            }
            if(pushRim)
            {
                var seat=Geom("wheelchair_seat");
                float side=Mathf.Sign(source.localPosition.x-(seat!=null?seat.localPosition.x:0)),offset=side*(halfWidth+.009f);
                Ring(wheel,"hand_propulsion_rim",Vector3.up*offset,Vector3.up,radius-.044f,.005f,.005f,steel);
                for(int i=0;i<6;i++)
                {
                    float a=2*Mathf.PI*i/6;
                    Vector3 p=new Vector3((radius-.044f)*Mathf.Cos(a),0,(radius-.044f)*Mathf.Sin(a));
                    Tube(wheel,"hand_rim_mount",p,p+Vector3.up*offset,.0025f,steel);
                }
            }
        }
        void Wheelchair()
        {
            foreach(var geom in Mirror.Manifest.geoms)
            {
                var source=Geom(geom.name); if(source==null) continue;
                if(geom.name.StartsWith("wheelchair_visual_spoke_",StringComparison.Ordinal) || geom.name.StartsWith("wheelchair_visual_hub_",StringComparison.Ordinal))
                { foreach(var renderer in source.GetComponentsInChildren<Renderer>()) renderer.enabled=false; }
                if(geom.name.StartsWith("wheel_",StringComparison.Ordinal) || geom.name=="wheelchair_visual_caster_left" || geom.name=="wheelchair_visual_caster_right")
                    Wheel(source,geom.native_size[0],geom.native_size[1],geom.name.StartsWith("wheel_",StringComparison.Ordinal));
                if(geom.name.StartsWith("wheelchair_visual_foot_plate_",StringComparison.Ordinal))
                {
                    var foot=DetailFrame(source,"VisualSkin_"+geom.name); Vector3 d=source.localScale;
                    Box(foot,"rounded_footplate",Vector3.zero,d,steel,.003f);
                    for(int i=0;i<6;i++)
                        Box(foot,"footplate_traction_strip",new Vector3(0,d.y*.5f+.0003f,(i-2.5f)*d.z/8),new Vector3(d.x*.82f,.0007f,.004f),rubber,.0002f);
                }
            }
            var seat=Geom("wheelchair_seat"); var back=Geom("wheelchair_back");
            if(seat==null || back==null) return;
            var cushion=DetailFrame(seat,"VisualSkin_wheelchair_seat"); Vector3 size=seat.localScale;
            Box(cushion,"seat_frame_pan",Vector3.down*(size.y*.5f-.006f),new Vector3(size.x,.012f,size.z),trim,.004f);
            Box(cushion,"upholstered_seat_cushion",Vector3.up*.006f,new Vector3(size.x,size.y-.012f,size.z),fabric,.011f);
            var backrest=DetailFrame(back,"VisualSkin_wheelchair_back"); Vector3 dBack=back.localScale;
            Box(backrest,"upholstered_backrest",Vector3.zero,dBack,fabric,.009f);
            foreach(float sign in new[]{-1f,1f})
                Box(backrest,"backrest_stitched_seam",new Vector3(sign*(dBack.x*.5f-.018f),0,dBack.z*.5f+.0002f),new Vector3(.0008f,dBack.y-.04f,.0004f),trim,.0001f);
            var frame=VisualRoot(seat.parent,"VisualSkin_wheelchair_tubular_frame"); frame.localPosition=seat.localPosition;
            float x=size.x*.5f-.015f;
            foreach(float sign in new[]{-1f,1f})
            {
                Transform rear=null;
                foreach(var geom in Mirror.Manifest.geoms)
                    if(geom.name.StartsWith("wheel_",StringComparison.Ordinal) &&
                        Mathf.Sign(geom.position[0]-seat.localPosition.x)==sign) rear=Geom(geom.name);
                var caster=Geom("wheelchair_visual_caster_"+(sign<0?"left":"right"));
                Vector3 a=new Vector3(sign*x,-size.y*.5f-.016f,-size.z*.38f),b=new Vector3(sign*x,a.y,size.z*.38f);
                Tube(frame,"seat_chassis_rail",a,b,.012f,steel);
                if(rear!=null)
                {
                    Vector3 axle=frame.InverseTransformPoint(rear.position); axle.x=sign*x;
                    Tube(frame,"rear_axle_support",a,axle,.012f,steel);
                }
                if(caster!=null)
                {
                    Vector3 fork=frame.InverseTransformPoint(caster.position)+Vector3.up*.072f;
                    Tube(frame,"front_chassis_down_tube",b,fork,.012f,steel);
                }
                Tube(frame,"folding_cross_brace",a,new Vector3(-sign*x,-.19f,size.z*.35f),.009f,steel);
                var grip=Geom("wheelchair_visual_push_handle_"+(sign<0?"left":"right"));
                if(grip!=null) foreach(var renderer in grip.GetComponentsInChildren<Renderer>()) renderer.sharedMaterial=rubber;
            }
        }
        void WorkSurfaces()
        {
            foreach(var geom in Mirror.Manifest.geoms)
            {
                string name=geom.name;
                bool counter=name.StartsWith("v3_counter",StringComparison.Ordinal),tray=name.StartsWith("tray_",StringComparison.Ordinal);
                bool leg=name.StartsWith("counter_leg_",StringComparison.Ordinal),support=name.StartsWith("v3_delivery_support_",StringComparison.Ordinal);
                bool plate=name.EndsWith("_plate",StringComparison.Ordinal) && (name.StartsWith("burger",StringComparison.Ordinal) || name.StartsWith("hotdog",StringComparison.Ordinal));
                bool grasp=name.Contains("_grasp_");
                if(!counter && !tray && !leg && !support && !plate && !grasp && name!="user_table") continue;
                var source=Geom(name); if(source==null || geom.type!=6) continue;
                var detail=DetailFrame(source,"VisualSkin_"+name); Vector3 size=source.localScale;
                if(plate)
                {
                    Box(detail,"formed_food_container",Vector3.zero,size,ceramic,.0035f);
                    Box(detail,"food_well_glaze",Vector3.up*(size.y*.5f+.0001f),
                        new Vector3(size.x-.012f,.0002f,size.z-.012f),cavity,.00005f);
                    Sweep(detail,"rolled_container_rim",RoundedLoop(size.x*.5f-.0025f,size.z*.5f-.0025f,.008f,
                        size.y*.5f-.0002f),.001f,ceramic);
                    continue;
                }
                if(name=="user_table")
                {
                    const float laminate=.001f;
                    Box(detail,"laminated_plywood_core",Vector3.down*(laminate*.5f),size-Vector3.up*laminate,wood,.008f);
                    Box(detail,"white_laminate_tabletop",Vector3.up*((size.y-laminate)*.5f),new Vector3(size.x-.001f,laminate,size.z-.001f),ceramic,.0003f);
                    continue;
                }
                Material material=counter?worktop:tray || leg?wood:support?steel:ceramic;
                Box(detail,"rounded_"+name,Vector3.zero,size,material,counter?.0015f:.0025f);
                if(grasp)
                    for(int rib=-2;rib<=2;rib++)
                        Box(detail,"molded_grip_texture",new Vector3(0,size.y*.5f+.0001f,rib*size.z/7),
                            new Vector3(size.x*.74f,.0002f,.0007f),cavity,.00005f);
                if(leg)
                    Box(detail,"leg_end_ferrule",Vector3.down*(size.y*.5f-.016f),new Vector3(size.x+.0008f,.032f,size.z+.0008f),steel,.001f);
            }
        }
        void CabinetRun(Transform parent,string name,float centerX,float front,float width,float depth,float top,Material finish,bool countertop)
        {
            const float toe=.10f,panel=.018f;
            float height=top-toe;
            Box(parent,name+"_recessed_plinth",new Vector3(centerX,toe*.5f,front+depth*.5f+.025f),new Vector3(width-.06f,toe,depth-.10f),trim,.002f);
            Box(parent,name+"_bottom",new Vector3(centerX,toe+panel*.5f,front+depth*.5f),new Vector3(width,panel,depth),finish,.002f);
            Box(parent,name+"_back",new Vector3(centerX,toe+height*.5f,front+depth-panel*.5f),new Vector3(width,height,panel),finish,.001f);
            foreach(float sign in new[]{-1f,1f})
                Box(parent,name+"_end",new Vector3(centerX+sign*(width-panel)*.5f,toe+height*.5f,front+depth*.5f),new Vector3(panel,height,depth),finish,.002f);
            int doors=Mathf.Max(1,Mathf.RoundToInt(width/.6f));float step=width/doors;
            for(int door=0;door<doors;door++)
            {
                float x=centerX-width*.5f+(door+.5f)*step;
                Box(parent,name+"_door",new Vector3(x,toe+height*.5f,front+.004f),new Vector3(step-.004f,height-.012f,panel),finish,.002f);
                Tube(parent,name+"_pull",new Vector3(x-.075f,top-.08f,front-.020f),new Vector3(x+.075f,top-.08f,front-.020f),.004f,steel);
            }
            if(countertop)
                Box(parent,name+"_worktop",new Vector3(centerX,top+.016f,front+depth*.5f),new Vector3(width+.04f,.032f,depth+.035f),worktop,.003f);
        }
        void RoomEnvironment()
        {
            var floor=Geom("floor");if(floor==null)return;
            var room=DetailFrame(floor,"VisualSkin_room",true);
            bool found=false;Bounds counter=new Bounds();
            foreach(var geom in Mirror.Manifest.geoms)
            {
                if(!geom.name.StartsWith("v3_counter_",StringComparison.Ordinal))continue;
                var source=Geom(geom.name);if(source==null)continue;
                foreach(float x in new[]{-.5f,.5f})foreach(float y in new[]{-.5f,.5f})foreach(float z in new[]{-.5f,.5f})
                {
                    Vector3 corner=room.InverseTransformPoint(source.TransformPoint(new Vector3(x,y,z)));
                    if(!found){counter=new Bounds(corner,Vector3.zero);found=true;}else counter.Encapsulate(corner);
                }
            }
            if(!found)throw new InvalidOperationException("Room art requires the actual counter bounds");
            var floorTile=Mat("matte_porcelain_floor",new Color(.59f,.61f,.60f),0,.26f);
            var grout=Mat("tile_grout",new Color(.39f,.42f,.41f),0,.15f);
            var wall=Mat("painted_plaster",new Color(.92f,.925f,.91f),0,.12f);
            var cabinet=Mat("island_satin_cabinet",new Color(.62f,.68f,.67f),0,.28f);
            var rearCabinet=Mat("muted_green_cabinet",new Color(.28f,.39f,.35f),0,.30f);
            var backsplash=Mat("glazed_backsplash",new Color(.79f,.83f,.81f),0,.45f);
            var daylight=Mat("window_daylight",new Color(.60f,.72f,.79f),0,.48f);
            daylight.SetColor("_EmissionColor",new Color(.18f,.23f,.26f));daylight.EnableKeyword("_EMISSION");
            var led=Mat("cabinet_task_light",new Color(.94f,.93f,.84f),0,.2f);
            led.SetColor("_EmissionColor",new Color(1.4f,1.35f,1.12f));led.EnableKeyword("_EMISSION");
            float cx=counter.center.x,rearFront=counter.max.z+1.0f,back=rearFront+.66f;
            const float roomWidth=6.4f,roomFront=-2.7f;
            float roomDepth=back+.12f-roomFront,midZ=(back+.12f+roomFront)*.5f;
            Box(room,"porcelain_floor",new Vector3(cx,-.010f,midZ),new Vector3(roomWidth,.020f,roomDepth),floorTile,.0005f);
            for(float x=cx-roomWidth*.5f+.6f;x<cx+roomWidth*.5f;x+=.6f)
                Box(room,"floor_joint",new Vector3(x,.0001f,midZ),new Vector3(.002f,.0002f,roomDepth-.002f),grout,.00005f);
            for(float z=roomFront+.6f;z<back+.12f;z+=.6f)
                Box(room,"floor_joint",new Vector3(cx,.0001f,z),new Vector3(roomWidth-.002f,.0002f,.002f),grout,.00005f);
            Box(room,"rear_plaster_wall",new Vector3(cx,1.5f,back+.05f),new Vector3(roomWidth,3f,.10f),wall,.001f);
            Box(room,"left_return_wall",new Vector3(cx-roomWidth*.5f,1.5f,midZ),new Vector3(.10f,3f,roomDepth),wall,.001f);
            var ceilingMaterial=Mat("painted_ceiling",new Color(.90f,.91f,.90f),0,.1f);
            var ceiling=MeshObject(room,"inward_facing_ceiling",MakeMesh("inward_facing_ceiling",new List<Vector3>{
                new Vector3(cx-roomWidth*.5f,3f,roomFront),new Vector3(cx+roomWidth*.5f,3f,roomFront),
                new Vector3(cx+roomWidth*.5f,3f,back+.12f),new Vector3(cx-roomWidth*.5f,3f,back+.12f)},
                new List<Vector2>{Vector2.zero,Vector2.right,Vector2.one,Vector2.up},new List<int>{0,1,2,0,2,3}),ceilingMaterial);
            ceiling.GetComponent<MeshRenderer>().shadowCastingMode=ShadowCastingMode.Off;
            Box(room,"rear_skirting",new Vector3(cx,.045f,back-.008f),new Vector3(roomWidth,.09f,.016f),ceramic,.001f);
            // All island carcass surfaces stay below the native worktop; the rear run is beyond a one-metre aisle.
            CabinetRun(room,"island",cx,counter.min.z+.025f,counter.size.x-.020f,counter.size.z-.055f,counter.min.y,cabinet,false);
            CabinetRun(room,"rear_run",cx,rearFront,3.6f,.60f,.88f,rearCabinet,true);
            Box(room,"backsplash_field",new Vector3(cx,1.19f,back-.012f),new Vector3(3.64f,.55f,.012f),backsplash,.001f);
            for(int row=1;row<5;row++)
                Box(room,"backsplash_joint",new Vector3(cx,.915f+row*.11f,back-.019f),new Vector3(3.63f,.0015f,.0005f),grout,.0001f);
            for(int row=0;row<5;row++)for(int column=0;column<12;column++)
            {
                float x=cx-1.8f+column*.30f+(row%2)*.15f;
                if(x<cx+1.8f)Box(room,"backsplash_joint",new Vector3(x,.97f+row*.11f,back-.019f),new Vector3(.0015f,.108f,.0005f),grout,.0001f);
            }
            for(int door=0;door<3;door++)
            {
                float x=cx+.25f+door*.52f;
                Box(room,"upper_cabinet_case",new Vector3(x,1.87f,back-.17f),new Vector3(.52f,.70f,.32f),cabinet,.003f);
                Box(room,"upper_cabinet_door",new Vector3(x,1.87f,back-.34f),new Vector3(.515f,.69f,.018f),ceramic,.002f);
                Tube(room,"upper_cabinet_pull",new Vector3(x-.16f,1.60f,back-.36f),new Vector3(x-.06f,1.60f,back-.36f),.0035f,steel);
            }
            Box(room,"under_cabinet_light",new Vector3(cx+.77f,1.514f,back-.25f),new Vector3(1.47f,.008f,.025f),led,.001f);
            float windowX=cx-.98f,windowY=1.76f,windowZ=back-.025f;
            Box(room,"daylight_window",new Vector3(windowX,windowY,windowZ),new Vector3(1.32f,.94f,.012f),daylight,.001f);
            foreach(float sign in new[]{-1f,1f})
            {
                Box(room,"window_side_frame",new Vector3(windowX+sign*.67f,windowY,windowZ-.015f),new Vector3(.035f,1.0f,.035f),ceramic,.001f);
                Box(room,"window_top_bottom_frame",new Vector3(windowX,windowY+sign*.49f,windowZ-.015f),new Vector3(1.37f,.035f,.035f),ceramic,.001f);
            }
            Box(room,"window_mullion",new Vector3(windowX,windowY,windowZ-.020f),new Vector3(.024f,.95f,.035f),ceramic,.001f);
            Box(room,"window_sill",new Vector3(windowX,windowY-.51f,windowZ-.045f),new Vector3(1.43f,.025f,.15f),ceramic,.002f);
            var lightObject=new GameObject("VisualOnlyKitchenCeilingFill");lightObject.transform.SetParent(room,false);
            lightObject.transform.localPosition=new Vector3(cx,2.45f,.05f);
            var light=lightObject.AddComponent<Light>();light.type=LightType.Point;light.range=5f;light.intensity=.50f;
            light.color=new Color(.96f,.97f,1);light.shadows=LightShadows.None;
            var key=GameObject.Find("WindowKey")?.GetComponent<Light>();
            if(key!=null){key.transform.rotation=Quaternion.Euler(45,-35,0);key.intensity=.95f;key.shadowStrength=.28f;key.shadowCustomResolution=4096;key.color=new Color(1,.98f,.95f);}
            var fill=GameObject.Find("SoftFill")?.GetComponent<Light>() ?? GameObject.Find("CoolFill")?.GetComponent<Light>();
            if(fill!=null){fill.transform.rotation=Quaternion.Euler(68,155,0);fill.intensity=.28f;fill.shadows=LightShadows.None;}
            RenderSettings.ambientSkyColor=new Color(.60f,.65f,.68f);
            RenderSettings.ambientEquatorColor=new Color(.45f,.48f,.47f);
            RenderSettings.ambientGroundColor=new Color(.28f,.31f,.29f);
            QualitySettings.shadowDistance=6;QualitySettings.shadowCascades=4;
        }
        void ConfigureGlass()
        {
            glass=Mat("smoked_transparent_observation_glass",new Color(.12f,.16f,.18f,.16f),.03f,.48f);
            glass.SetOverrideTag("RenderType","Transparent");
            if(glass.HasProperty("_Mode")) glass.SetFloat("_Mode",2);
            if(glass.HasProperty("_Surface")) glass.SetFloat("_Surface",1);
            if(glass.HasProperty("_Blend")) glass.SetFloat("_Blend",0);
            if(glass.HasProperty("_BaseColor")) glass.SetColor("_BaseColor",glass.color);
            glass.SetInt("_SrcBlend",(int)BlendMode.SrcAlpha); glass.SetInt("_DstBlend",(int)BlendMode.OneMinusSrcAlpha);
            glass.SetInt("_ZWrite",0); glass.renderQueue=(int)RenderQueue.Transparent;
            glass.DisableKeyword("_ALPHATEST_ON"); glass.DisableKeyword("_ALPHAPREMULTIPLY_ON");
            glass.EnableKeyword("_ALPHABLEND_ON");
            if(GraphicsSettings.currentRenderPipeline!=null) glass.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
        }
        void BrushedFinish(Material material)
        {
            const int width=128;
            var normal=new Texture2D(width,width,TextureFormat.RGBA32,true,true) {name="owned_brushed_steel_normal",wrapMode=TextureWrapMode.Repeat,
                filterMode=FilterMode.Trilinear,anisoLevel=4};
            var pixels=new Color[width*width];
            for(int y=0;y<width;y++) for(int x=0;x<width;x++)
            {
                float phase=2*Mathf.PI/width;
                float grain=.010f*Mathf.Sin(x*phase*12)+.004f*Mathf.Sin((x*3+y)*phase);
                pixels[y*width+x]=new Color(.5f+grain,.5f,1,.5f+grain);
            }
            normal.SetPixels(pixels);normal.Apply(true,false);owned.Add(normal);
            material.SetTexture("_BumpMap",normal);material.SetFloat("_BumpScale",.18f);material.EnableKeyword("_NORMALMAP");
            material.SetTextureScale("_BumpMap",new Vector2(4,2));
        }
        public void Apply()
        {
            if(applied)return;
            if(Mirror==null)Mirror=GetComponent<KitchenSceneMirror>();
            if(Mirror==null)throw new InvalidOperationException("Kitchen visual skin needs its scene mirror");
            if(!Mirror.Built)Mirror.Build();
            shader=GraphicsSettings.currentRenderPipeline==null?Shader.Find("Standard"):Shader.Find("Universal Render Pipeline/Lit");
            if(shader==null)throw new InvalidOperationException("Platform PBR shader missing");
            steel=Mat("brushed_stainless",new Color(.48f,.52f,.54f),.78f,.50f);
            BrushedFinish(steel);
            ceramic=Mat("porcelain",new Color(.89f,.91f,.91f),0,.74f);
            cavity=Mat("warm_white_cavity_enamel",new Color(.83f,.82f,.78f),0,.40f);
            gasket=Mat("silicone_door_gasket",new Color(.09f,.10f,.10f),0,.15f);
            enamel=Mat("appliance_satin_enamel",new Color(.46f,.49f,.50f),.18f,.36f);
            trim=Mat("graphite_appliance_trim",new Color(.035f,.043f,.046f),.15f,.34f);
            fabric=Mat("wheelchair_woven_upholstery",new Color(.075f,.19f,.18f),0,.14f);
            worktop=Mat("composite_worktop",new Color(.64f,.67f,.65f),0,.22f);
            rubber=Mat("rubber",new Color(.045f,.05f,.055f),0,.12f);
            green=Mat("leaf_lettuce",new Color(.18f,.39f,.07f),0,.23f);
            stem=Mat("fruit_stem",new Color(.19f,.13f,.045f));
            bread=Mat("baked_bread",new Color(.68f,.39f,.13f),0,.17f);
            meat=Mat("grilled_patty",new Color(.15f,.065f,.028f),0,.2f);
            cheese=Mat("cheese_mustard",new Color(.96f,.67f,.055f),0,.2f);
            red=Mat("sausage",new Color(.55f,.17f,.07f),0,.4f);
            wood=Mat("CC0_fine_grained_wood",Color.white,0,.29f);
            var texture=Resources.Load<Texture2D>("KitchenPlatformV3/Textures/wood_Diffuse");
            if(texture==null)throw new InvalidOperationException("Licensed wood material missing");
            wood.mainTexture=texture;
            var normal=Resources.Load<Texture2D>("KitchenPlatformV3/Textures/wood_nor_gl");
            if(normal!=null){wood.SetTexture("_BumpMap",normal);wood.SetFloat("_BumpScale",.25f);wood.EnableKeyword("_NORMALMAP");}
            wood.mainTextureScale=new Vector2(2,2);
            ConfigureGlass();
            foreach(var geom in Mirror.Manifest.geoms)
            {
                string n=geom.name; Material material=null;
                if(n.StartsWith("tray_",StringComparison.Ordinal) || n.StartsWith("counter_leg_",StringComparison.Ordinal))material=wood;
                else if(n.StartsWith("sink_",StringComparison.Ordinal) || n.StartsWith("faucet_",StringComparison.Ordinal) ||
                    n.StartsWith("microwave_",StringComparison.Ordinal) || n.StartsWith("basket_",StringComparison.Ordinal) ||
                    n.StartsWith("wheelchair_visual_",StringComparison.Ordinal) || n.StartsWith("v3_delivery_support_",StringComparison.Ordinal))material=steel;
                else if(n.Contains("_plate") || n.Contains("_grasp_") || n=="user_table")material=ceramic;
                else if(n.StartsWith("wheel_",StringComparison.Ordinal))material=rubber;
                else if(n=="wheelchair_seat" || n=="wheelchair_back")material=fabric;
                else if(n.StartsWith("v3_counter",StringComparison.Ordinal))material=worktop;
                if(material==null) continue;
                var source=Geom(n);
                if(source!=null) foreach(var renderer in source.GetComponentsInChildren<Renderer>()) renderer.sharedMaterial=material;
            }
            Foods(); Microwave(); SinkAndBasket(); Faucet(); Wheelchair(); WorkSurfaces(); RoomEnvironment();
            BatchDetails(); applied=true;
        }
        void OnDestroy(){foreach(var item in owned)if(item!=null)Destroy(item);}
    }
}
