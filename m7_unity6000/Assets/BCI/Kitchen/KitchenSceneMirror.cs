using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Rendering;

namespace BCIIntelligentRobot.Kitchen
{
    [Serializable] public sealed class KitchenBody
    {
        public int id; public string name; public float[] position; public float[] quaternion_wxyz;
    }
    [Serializable] public sealed class KitchenGeom
    {
        public int id; public string name; public int body_id; public int type; public string mesh;
        public float[] position; public float[] quaternion_wxyz; public float[] native_size; public float[] rgba;
    }
    [Serializable] public sealed class KitchenManifest
    {
        public int schema; public string transform_id; public string model_source_sha256;
        public string model_builder_sha256; public string model_compiled_sha256; public string model_scene_sha256;
        public string layout_id; public string layout_manifest_sha256; public string layout_code_sha256; public int seed;
        public KitchenBody[] bodies; public KitchenGeom[] geoms;
    }
    [Serializable] public sealed class KitchenWorld
    {
        public string type; public int schema; public string runtimeSession; public string sceneEpoch;
        public string sceneHash; public long sequence; public long simTick;
        public bool stopped; public string stop_reason; public KitchenBody[] bodies; public float[] state16;
        public string recording_status; public string episode_id;
    }

    // All geometry is visual. Only the MuJoCo server owns motion and contact.
    public sealed class KitchenSceneMirror : MonoBehaviour
    {
        public string resourceRoot = "KitchenModel";
        public KitchenManifest Manifest { get; private set; }
        public long LastTick { get; private set; } = -1;
        public long LastSequence { get; private set; } = -1;
        public string LastEpoch { get; private set; } = "";
        public string LastRuntimeSession { get; private set; } = "";
        readonly Dictionary<int, Transform> bodies = new Dictionary<int, Transform>();
        readonly Dictionary<string, Mesh> meshes = new Dictionary<string, Mesh>();
        readonly List<Material> materials = new List<Material>();
        public bool Built => Manifest != null && bodies.Count == Manifest.bodies.Length;

        public static Vector3 NativeVector(float[] value) => new Vector3(value[0], value[2], value[1]);
        static Quaternion ReadQuaternion(float[] q) => new Quaternion(q[1], q[2], q[3], q[0]);
        public static Quaternion NativeRotation(float[] q)
        {
            Matrix4x4 c = Matrix4x4.zero;
            c[0, 0] = 1; c[1, 2] = 1; c[2, 1] = 1; c[3, 3] = 1;
            return (c * Matrix4x4.Rotate(ReadQuaternion(q)) * c).rotation;
        }
        void Awake() { if (!Built) Build(); }
        static void RemoveCollider(GameObject item)
        {
            Collider collider = item.GetComponent<Collider>();
            if (collider == null) return;
            if (Application.isPlaying) Destroy(collider); else DestroyImmediate(collider);
        }
        GameObject Primitive(PrimitiveType type, Transform parent, string label)
        {
            GameObject item = GameObject.CreatePrimitive(type);
            item.name = label; item.transform.SetParent(parent, false); RemoveCollider(item); return item;
        }
        // Smooth audited housing normals without welding geometry or crossing hard edges.
        static bool IsFr3HousingMesh(string key)
        {
            return key=="mesh_932000e238db6a13e5fd" || key=="mesh_b105984cb0c880da2071" ||
                key=="mesh_f90151ee684751dbe14c" || key=="mesh_afddb246742996bebcdb" ||
                key=="mesh_09e5216ee01ae78cbdab";
        }
        static Vector3[] Fr3HousingNormals(Vector3[] vertices,int[] triangles,Vector3[] imported)
        {
            int count=vertices.Length;
            if(count%3!=0 || triangles.Length!=count || imported.Length!=count) return imported;
            for(int i=0;i<count;i++) if(triangles[i]!=i) return imported;
            var groups=new Dictionary<Vector3,List<int>>();
            var faces=new Vector3[count]; var weights=new float[count];
            for(int i=0;i<count;i+=3)
            {
                Vector3 cross=Vector3.Cross(vertices[i+1]-vertices[i],vertices[i+2]-vertices[i]);
                float length=cross.magnitude;
                if(length<=1e-20f) continue;
                Vector3 face=cross/length;
                for(int corner=0;corner<3;corner++)
                {
                    int index=i+corner;
                    Vector3 u=vertices[i+(corner+1)%3]-vertices[index];
                    Vector3 v=vertices[i+(corner+2)%3]-vertices[index];
                    faces[index]=face;
                    weights[index]=Mathf.Atan2(Vector3.Cross(u,v).magnitude,Vector3.Dot(u,v));
                    if(!groups.TryGetValue(vertices[index],out var entries))
                        groups.Add(vertices[index],entries=new List<int>());
                    entries.Add(index);
                }
            }
            var result=(Vector3[])imported.Clone();
            float threshold=Mathf.Cos(35f*Mathf.Deg2Rad);
            foreach(var entries in groups.Values) foreach(int index in entries)
            {
                Vector3 sum=Vector3.zero;
                foreach(int other in entries)
                    if(Vector3.Dot(faces[index],faces[other])>=threshold)
                        sum+=faces[other]*weights[other];
                float length=sum.magnitude;
                if(length>1e-20f) result[index]=sum/length;
            }
            return result;
        }
        Mesh LoadMesh(string key)
        {
            if (meshes.TryGetValue(key, out Mesh cached)) return cached;
            TextAsset asset = Resources.Load<TextAsset>(resourceRoot + "/" + key);
            if (asset == null) throw new InvalidOperationException("Missing compiled mesh " + key);
            using (var stream = new MemoryStream(asset.bytes))
            using (var reader = new BinaryReader(stream))
            {
                byte[] magic = reader.ReadBytes(8);
                if (magic[0] != 'K' || magic[1] != 'M' || magic[2] != 'S' || magic[3] != 'H' || magic[4] != '1')
                    throw new InvalidDataException("Invalid kitchen mesh header");
                int count = checked((int)reader.ReadUInt32());
                if (count <= 0 || count % 3 != 0 || stream.Length != 12L + count * 24L)
                    throw new InvalidDataException("Invalid compiled kitchen mesh length");
                Vector3[] vertices = new Vector3[count], normals = new Vector3[count];
                int[] triangles = new int[count];
                for (int i = 0; i < count; i++) vertices[i] = new Vector3(reader.ReadSingle(), reader.ReadSingle(), reader.ReadSingle());
                for (int i = 0; i < count; i++) { normals[i] = new Vector3(reader.ReadSingle(), reader.ReadSingle(), reader.ReadSingle()); triangles[i] = i; }
                if (IsFr3HousingMesh(key)) normals = Fr3HousingNormals(vertices, triangles, normals);
                Mesh mesh = new Mesh { name = key, indexFormat = IndexFormat.UInt32 };
                mesh.vertices = vertices; mesh.normals = normals; mesh.triangles = triangles; mesh.RecalculateBounds();
                meshes.Add(key, mesh); return mesh;
            }
        }
        public void Build()
        {
            if (Built) return;
            TextAsset json = Resources.Load<TextAsset>(resourceRoot + "/scene");
            if (json == null) throw new InvalidOperationException("Kitchen actual-model manifest missing");
            KitchenManifest manifest = JsonUtility.FromJson<KitchenManifest>(json.text);
            if (manifest.schema != 1 || manifest.transform_id != "kitchen_C_xzy_1to1_v1")
                throw new InvalidDataException("Kitchen coordinate contract mismatch");
            string layoutId = string.IsNullOrWhiteSpace(manifest.layout_id) ? "canonical" : manifest.layout_id;
            if (layoutId != "canonical" && layoutId != "microwave_front_food_row_v1" &&
                !layoutId.StartsWith("kitchen_v3_norice_candidate_", StringComparison.Ordinal))
                throw new InvalidDataException("Kitchen layout identity is unknown");
            if (layoutId != "canonical" && (string.IsNullOrWhiteSpace(manifest.model_scene_sha256) ||
                string.IsNullOrWhiteSpace(manifest.layout_manifest_sha256) ||
                string.IsNullOrWhiteSpace(manifest.layout_code_sha256) ||
                string.IsNullOrWhiteSpace(manifest.model_compiled_sha256)))
                throw new InvalidDataException("Kitchen variant is missing its complete scene identity");
            Shader shader = GraphicsSettings.currentRenderPipeline == null ? Shader.Find("Standard") : Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null) throw new InvalidOperationException("No supported visual material shader");
            foreach (KitchenBody body in manifest.bodies)
            {
                GameObject node = new GameObject(body.name);
                node.transform.SetParent(transform, false);
                node.transform.localPosition = new Vector3(body.position[0], body.position[1], body.position[2]);
                node.transform.localRotation = ReadQuaternion(body.quaternion_wxyz);
                bodies.Add(body.id, node.transform);
            }
            foreach (KitchenGeom geom in manifest.geoms)
            {
                if (!bodies.TryGetValue(geom.body_id, out Transform parent)) throw new InvalidDataException("Missing body");
                GameObject item;
                float[] size = geom.native_size;
                if (geom.type == 7)
                {
                    item = new GameObject(geom.name);
                    item.transform.SetParent(parent, false);
                    item.AddComponent<MeshFilter>().sharedMesh = LoadMesh(geom.mesh);
                    item.AddComponent<MeshRenderer>();
                }
                else if (geom.type == 3)
                {
                    item = new GameObject(geom.name); item.transform.SetParent(parent, false);
                    GameObject tube = Primitive(PrimitiveType.Cylinder, item.transform, "tube");
                    tube.transform.localScale = new Vector3(2 * size[0], size[1], 2 * size[0]);
                    foreach (float sign in new float[] { -1, 1 })
                    {
                        GameObject end = Primitive(PrimitiveType.Sphere, item.transform, "end");
                        end.transform.localPosition = Vector3.up * (sign * size[1]);
                        end.transform.localScale = Vector3.one * (2 * size[0]);
                    }
                }
                else
                {
                    PrimitiveType type = geom.type == 6 ? PrimitiveType.Cube : geom.type == 0 ? PrimitiveType.Plane :
                        geom.type == 5 ? PrimitiveType.Cylinder : PrimitiveType.Sphere;
                    item = Primitive(type, parent, geom.name);
                    if (geom.type == 6) item.transform.localScale = new Vector3(2 * size[0], 2 * size[2], 2 * size[1]);
                    else if (geom.type == 0) item.transform.localScale = new Vector3(size[0] / 5, 1, size[1] / 5);
                    else if (geom.type == 5) item.transform.localScale = new Vector3(2 * size[0], size[1], 2 * size[0]);
                    else if (geom.type == 4) item.transform.localScale = new Vector3(2 * size[0], 2 * size[2], 2 * size[1]);
                    else item.transform.localScale = Vector3.one * (2 * size[0]);
                }
                item.transform.localPosition = new Vector3(geom.position[0], geom.position[1], geom.position[2]);
                item.transform.localRotation = ReadQuaternion(geom.quaternion_wxyz);
                Material material = new Material(shader) { color = new Color(geom.rgba[0], geom.rgba[1], geom.rgba[2], geom.rgba[3]) };
                materials.Add(material);
                foreach (Renderer renderer in item.GetComponentsInChildren<Renderer>(true)) renderer.sharedMaterial = material;
            }
            Manifest = manifest;
            transform.localScale = Vector3.one;
        }
        public void Apply(KitchenWorld world)
        {
            if (!Built) Build();
            if (world == null || world.schema != 1 || world.bodies == null) throw new InvalidDataException("Invalid world snapshot");
            if (world.sceneHash != Manifest.model_scene_sha256) throw new InvalidDataException("World/model scene hash mismatch");
            if (string.IsNullOrWhiteSpace(world.runtimeSession) || string.IsNullOrWhiteSpace(world.sceneEpoch))
                throw new InvalidDataException("World session identity is missing");
            if (world.bodies.Length != bodies.Count) throw new InvalidDataException("World/model body count mismatch");
            var seen = new HashSet<int>();
            foreach (KitchenBody body in world.bodies)
            {
                if (!bodies.ContainsKey(body.id) || !seen.Add(body.id) || body.position == null || body.position.Length != 3 ||
                    body.quaternion_wxyz == null || body.quaternion_wxyz.Length != 4)
                    throw new InvalidDataException("Incomplete/duplicate body snapshot");
                foreach (float x in body.position) if (float.IsNaN(x) || float.IsInfinity(x)) throw new InvalidDataException("Nonfinite body position");
                float norm = 0;
                foreach (float x in body.quaternion_wxyz) { if (float.IsNaN(x) || float.IsInfinity(x)) throw new InvalidDataException("Nonfinite rotation"); norm += x*x; }
                if (Mathf.Abs(norm-1) > .001f) throw new InvalidDataException("Nonunit body rotation");
            }
            if (world.sceneEpoch != LastEpoch || world.runtimeSession != LastRuntimeSession)
            {
                LastEpoch = world.sceneEpoch;
                LastRuntimeSession = world.runtimeSession;
                LastTick = -1;
                LastSequence = -1;
            }
            if (world.simTick < LastTick || world.sequence <= LastSequence) return;
            foreach (KitchenBody body in world.bodies)
            {
                if (!bodies.TryGetValue(body.id, out Transform target)) throw new InvalidDataException("Unknown body identity");
                target.localPosition = NativeVector(body.position);
                target.localRotation = NativeRotation(body.quaternion_wxyz);
            }
            LastTick = world.simTick;
            LastSequence = world.sequence;
        }
        void OnDestroy()
        {
            foreach (Mesh mesh in meshes.Values) if (mesh != null) Destroy(mesh);
            foreach (Material material in materials) if (material != null) Destroy(material);
        }
    }
}

