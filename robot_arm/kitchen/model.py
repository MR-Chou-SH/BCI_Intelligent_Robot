"""Named dual-arm model composition and independent kitchen collision geometry."""
from __future__ import annotations
from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import json
import math
import numpy as np
import mujoco
from .layouts import apply_layout, layout_manifest, layout_manifest_sha256
from .scene_identity import compiled_model_sha256, scene_identity_sha256

ROOT = Path(__file__).resolve().parents[2]
MJCF = ROOT / "robot_arm/assets/fr3_umi_merged.xml"
HOME = np.array([0, 0, 0, -math.pi / 2, 0, math.pi / 2, -math.pi / 4])
ARMS = ("left", "right")
TABLE_Z = .80

@dataclass(frozen=True)
class ArmHandle:
    name: str
    joints: tuple[int, ...]
    qpos: tuple[int, ...]
    dofs: tuple[int, ...]
    actuators: tuple[int, ...]
    fingers: tuple[int, int]
    gripper_actuator: int
    tcp_body: int
    pad_geoms: tuple[int, int]
    tcp_offset: tuple[float,float,float] = (0.,.003,.1244)

    @classmethod
    def resolve(cls, model, name):
        def identity(kind, text):
            result = mujoco.mj_name2id(model, kind, name + "_" + text)
            if result < 0:
                raise ValueError("Missing named element: " + name + "_" + text)
            return int(result)
        joints = tuple(identity(mujoco.mjtObj.mjOBJ_JOINT, "fr3_joint" + str(i)) for i in range(1, 8))
        fingers = tuple(identity(mujoco.mjtObj.mjOBJ_JOINT, "umi_" + side + "_finger_joint") for side in ("left", "right"))
        pads = []
        for side in ("left", "right"):
            mesh_name = name + "_umi_" + side + "_finger"
            candidates = [i for i in range(model.ngeom)
                if model.geom_type[i] == mujoco.mjtGeom.mjGEOM_MESH
                and model.geom_contype[i] != 0
                and model.mesh(int(model.geom_dataid[i])).name == mesh_name]
            if len(candidates) != 1:
                raise ValueError("Expected one collision pad mesh: " + mesh_name)
            pads.append(candidates[0])
        return cls(name, joints, tuple(int(model.jnt_qposadr[j]) for j in joints),
            tuple(int(model.jnt_dofadr[j]) for j in joints),
            tuple(identity(mujoco.mjtObj.mjOBJ_ACTUATOR, "fr3_joint" + str(i)) for i in range(1, 8)),
            tuple(int(model.jnt_qposadr[j]) for j in fingers),
            identity(mujoco.mjtObj.mjOBJ_ACTUATOR, "umi_fingers_actuator"),
            identity(mujoco.mjtObj.mjOBJ_BODY, "umi_umi_gripper_base"), tuple(pads))

@dataclass
class KitchenModel:
    model: object
    data: object
    arms: dict[str, ArmHandle]
    objects: dict[str, int]
    spec: object
    source_sha256: str
    seed: int
    layout_id: str = "canonical"
    layout_sha256: str = ""
    compiled_model_sha256: str = ""
    scene_sha256: str = ""
    builder_sha256: str = ""

def _box(body, name, half, pos, rgba=(.7,.7,.7,1), mass=None, collision=True):
    kwargs = dict(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, size=half, pos=pos,
                  rgba=rgba, friction=[.8,.01,.001], solref=[.02,1])
    if mass is not None: kwargs["mass"] = mass
    if not collision: kwargs.update(contype=0,conaffinity=0)
    return body.add_geom(**kwargs)

def _visual_capsule(body, name, start, end, radius, rgba):
    """Add a decorative link that cannot participate in contact physics."""
    return body.add_geom(name=name,type=mujoco.mjtGeom.mjGEOM_CAPSULE,
        fromto=[*start,*end],size=[radius],rgba=rgba,contype=0,conaffinity=0)

def _wheelchair_visual_details(spec):
    """Decorative wheelchair geometry enabled only by an explicit scene variant."""
    wb=spec.worldbody
    chair_frame=[.34,.37,.39,1]
    chair_metal=[.59,.62,.64,1]
    wheel_axis_quat=[math.sqrt(.5),0,math.sqrt(.5),0]
    for side,x in (("left",-.302),("right",.302)):
        wb.add_geom(name="wheelchair_visual_hub_"+side,
            type=mujoco.mjtGeom.mjGEOM_CYLINDER,size=[.034,.010],
            pos=[x,-1.05,.28],quat=wheel_axis_quat,rgba=chair_metal,
            contype=0,conaffinity=0)
        for spoke in range(8):
            angle=2*math.pi*spoke/8
            end=[x,-1.05+.222*math.cos(angle),.28+.222*math.sin(angle)]
            _visual_capsule(wb,"wheelchair_visual_spoke_"+side+"_"+str(spoke),
                [x,-1.05,.28],end,.0035,chair_metal)
    for side,x in (("left",-.24),("right",.24)):
        _visual_capsule(wb,"wheelchair_visual_armrest_rail_"+side,
            [x,-1.08,.605],[x,-.73,.605],.014,chair_frame)
        _visual_capsule(wb,"wheelchair_visual_armrest_support_a_"+side,
            [x,-1.04,.48],[x,-1.04,.59],.010,chair_frame)
        _visual_capsule(wb,"wheelchair_visual_armrest_support_b_"+side,
            [x,-.78,.48],[x,-.78,.59],.010,chair_frame)
        _visual_capsule(wb,"wheelchair_visual_back_cane_"+side,
            [x,-1.215,.58],[x,-1.255,.99],.012,chair_frame)
        _visual_capsule(wb,"wheelchair_visual_push_handle_"+side,
            [x,-1.255,.97],[x,-1.31,.97],.012,chair_metal)
        foot_x=x*.55
        _visual_capsule(wb,"wheelchair_visual_foot_hanger_"+side,
            [foot_x,-.91,.44],[foot_x,-.65,.21],.010,chair_frame)
        _box(wb,"wheelchair_visual_foot_plate_"+side,[.078,.105,.009],
            [foot_x,-.61,.17],[.36,.38,.39,1],collision=False)
        caster_x=x*.67
        _visual_capsule(wb,"wheelchair_visual_caster_fork_"+side,
            [caster_x,-.72,.18],[caster_x,-.72,.115],.009,chair_metal)
        wb.add_geom(name="wheelchair_visual_caster_"+side,
            type=mujoco.mjtGeom.mjGEOM_CYLINDER,size=[.052,.018],
            pos=[caster_x,-.72,.095],quat=wheel_axis_quat,
            rgba=[.16,.17,.18,1],contype=0,conaffinity=0)
    _visual_capsule(wb,"wheelchair_visual_rear_crossbar",
        [-.20,-1.17,.53],[.20,-1.17,.53],.010,chair_frame)

def _look_at(position, target):
    z = np.asarray(position, dtype=float) - target
    z /= np.linalg.norm(z)
    x = np.cross([0.,0.,1.], z)
    x /= np.linalg.norm(x)
    y = np.cross(z,x)
    quat=np.zeros(4); mujoco.mju_mat2Quat(quat,np.column_stack([x,y,z]).ravel())
    return quat

def _kitchen(spec, rng, *, include_rice=True):
    wb=spec.worldbody
    wb.add_geom(name="floor",type=mujoco.mjtGeom.mjGEOM_PLANE,size=[2,2,.01],rgba=[.75,.74,.72,1])
    # Four slabs leave a real sink opening.
    for name,x0,x1,y0,y1 in (
        ("counter_left",-.85,-.78,-.45,.45),("counter_right",-.42,.85,-.45,.45),
        ("counter_front",-.78,-.42,-.45,-.09),("counter_back",-.78,-.42,.25,.45)):
        _box(wb,name,[(x1-x0)/2,(y1-y0)/2,.025],[(x1+x0)/2,(y1+y0)/2,TABLE_Z-.025],[.83,.80,.74,1])
    for x in (-.78,.78):
        for y in (-.37,.37):
            _box(wb,"counter_leg_"+str(x)+"_"+str(y),[.035,.035,.375],[x,y,.375],[.48,.35,.24,1])
    sink=wb.add_body(name="sink",pos=[-.60,.08,TABLE_Z])
    _box(sink,"sink_floor",[.18,.17,.008],[0,0,-.16],[.45,.48,.5,1])
    for i,(half,pos) in enumerate((
        ([.008,.17,.08],[-.18,0,-.08]),([.008,.17,.08],[.18,0,-.08]),
        ([.18,.008,.08],[0,-.17,-.08]),([.18,.008,.08],[0,.17,-.08]))):
        _box(sink,"sink_wall_"+str(i),half,pos,[.52,.55,.56,1])
    basket=wb.add_body(name="basket",pos=[-.60,.08,.66])
    _box(basket,"basket_bottom",[.125,.115,.008],[0,0,0],[.58,.60,.61,1])
    for i,(half,pos) in enumerate((
        ([.005,.115,.035],[-.125,0,.035]),([.005,.115,.035],[.125,0,.035]),
        ([.125,.005,.035],[0,-.115,.035]),([.125,.005,.035],[0,.115,.035]))):
        _box(basket,"basket_wall_"+str(i),half,pos,[.58,.60,.61,1])
    faucet=wb.add_body(name="faucet",pos=[-.60,.31,.80])
    faucet.add_geom(name="faucet_stem",type=mujoco.mjtGeom.mjGEOM_CAPSULE,
        fromto=[0,0,0,0,0,.25],size=[.014],rgba=[.6,.65,.67,1])
    faucet.add_geom(name="faucet_spout",type=mujoco.mjtGeom.mjGEOM_CAPSULE,
        fromto=[0,0,.25,0,-.20,.25],size=[.014],rgba=[.6,.65,.67,1])
    handle=faucet.add_body(name="faucet_handle",pos=[.105,0,.10])
    handle.add_joint(name="faucet_switch",type=mujoco.mjtJoint.mjJNT_HINGE,axis=[0,0,1],
        range=[0,math.pi/3],damping=.03,frictionloss=.03)
    handle.add_geom(name="faucet_handle_geom",type=mujoco.mjtGeom.mjGEOM_CAPSULE,
        fromto=[0,0,0,0,-.085,0],size=[.012],mass=.06,rgba=[.6,.65,.67,1])
    micro=wb.add_body(name="microwave",pos=[.64,.14,.80])
    for name,half,pos in (
        ("bottom",[.20,.18,.012],[0,0,.012]),("top",[.20,.18,.012],[0,0,.328]),
        ("back",[.012,.18,.17],[.188,0,.17]),("side_front",[.20,.012,.17],[0,-.168,.17]),
        ("side_back",[.20,.012,.17],[0,.168,.17])):
        _box(micro,"microwave_"+name,half,pos,[.28,.30,.32,1])
    door=micro.add_body(name="microwave_door",pos=[-.217,-.155,.02])
    door.add_joint(name="microwave_hinge",type=mujoco.mjtJoint.mjJNT_HINGE,
        axis=[0,0,1],range=[0,math.radians(100)],damping=.15,frictionloss=.03)
    _box(door,"microwave_door_panel",[.008,.14,.145],[0,.14,.145],[.17,.19,.20,1],mass=.55)
    _box(door,"microwave_door_handle",[.015,.012,.06],[-.025,.25,.145],[.55,.56,.57,1],mass=.05)
    button=micro.add_body(name="microwave_button",pos=[-.212,.155,.16])
    button.add_joint(name="microwave_start_slide",type=mujoco.mjtJoint.mjJNT_SLIDE,
        axis=[1,0,0],range=[0,.005],damping=2,stiffness=250)
    _box(button,"microwave_start_geom",[.006,.013,.013],[0,0,0],[.2,.75,.25,1],mass=.02)
    # Static wheelchair and support surface, without a person.
    _box(wb,"user_table",[.30,.20,.015],[0,-.64,.745],[.73,.74,.75,1])
    _box(wb,"wheelchair_seat",[.23,.21,.025],[0,-1.02,.47],[.15,.16,.18,1])
    _box(wb,"wheelchair_back",[.23,.025,.24],[0,-1.23,.73],[.15,.16,.18,1])
    for x in (-.27,.27):
        wb.add_geom(name="wheel_"+str(x),type=mujoco.mjtGeom.mjGEOM_CYLINDER,
            size=[.28,.025],pos=[x,-1.05,.28],quat=[math.sqrt(.5),0,math.sqrt(.5),0],rgba=[.12,.13,.14,1])
    tray=wb.add_body(name="wood_tray",pos=[0,-.20,.812])
    tray.add_freejoint(name="wood_tray_free")
    _box(tray,"tray_bottom",[.24,.16,.008],[0,0,0],[.65,.40,.19,1],mass=.45)
    for i,(half,pos) in enumerate((
        ([.008,.16,.015],[-.24,0,.015]),([.008,.16,.015],[.24,0,.015]),
        ([.24,.008,.015],[0,-.16,.015]),([.24,.008,.015],[0,.16,.015]))):
        _box(tray,"tray_rim_"+str(i),half,pos,[.65,.40,.19,1],mass=.025)
    for side,x in (("left",-.27),("right",.27)):
        # Flat side handles, extended 75mm beyond the rim so the inner finger
        # clears the tray wall. Raised bars were tried for B1 but obstructed the
        # right arm's oven transfer, so the low profile is kept.
        _box(tray,"tray_handle_"+side,[.022,.055,.016],[x,0,.046],[.65,.40,.19,1],mass=.05)
        tray.add_site(name="tray_grasp_"+side,pos=[x,0,.046],size=[.004],rgba=[1,0,0,1])
    records=[
        ("apple",[-.73,-.29,.835],.15,[.035,.035,.035],[.78,.04,.02,1]),
        ("pear",[-.55,-.29,.845],.18,[.032,.032,.045],[.75,.75,.13,1]),
        ("banana",[-.68,-.15,.823],.12,None,[.95,.78,.1,1]),
        ("tomato",[-.37,-.30,.832],.12,[.032,.032,.030],[.9,.05,.02,1]),
    ]
    for name,pos,mass,half,color in records:
        pos=np.array(pos)+[rng.uniform(-.015,.015),rng.uniform(-.015,.015),0]
        body=wb.add_body(name=name,pos=pos); body.add_freejoint(name=name+"_free")
        if half:
            body.add_geom(name=name+"_collision",type=mujoco.mjtGeom.mjGEOM_ELLIPSOID,
                size=half,mass=mass,rgba=color,friction=[.8,.01,.001],solref=[.02,1])
        else:
            for j,(a,b) in enumerate((([-.07,-.015,0],[-.025,0,.005]),([-.025,0,.005],[.025,0,.005]),([.025,0,.005],[.07,-.015,0]))):
                body.add_geom(name=name+"_"+str(j),type=mujoco.mjtGeom.mjGEOM_CAPSULE,
                    fromto=a+b,size=[.017],mass=mass/3,rgba=color,friction=[.8,.01,.001])
    for name,x,y in (("burger",.42,-.25),("hotdog",.61,-.25),("rice",.75,-.365)):
        if name == "rice" and not include_rice:
            continue
        plate=wb.add_body(name=name+"_container",pos=[x,y,.81])
        plate.add_freejoint(name=name+"_container_free")
        _box(plate,name+"_plate",[.066,.055,.008],[0,0,0],[.85,.84,.80,1],mass=.12)
        for side,dx in (("left",-.073),("right",.073)):
            _box(plate,name+"_grasp_"+side,[.012,.025,.01],[dx,0,.01],[.85,.84,.80,1],mass=.01)
        if name=="rice":
            plate.add_geom(name="rice_rigid_approximation",type=mujoco.mjtGeom.mjGEOM_ELLIPSOID,
                size=[.047,.042,.03],pos=[0,0,.035],mass=.21,rgba=[.95,.94,.86,1])
        else:
            food=wb.add_body(name=name,pos=[x,y,.85]); food.add_freejoint(name=name+"_free")
            food.add_geom(name=name+"_food",type=mujoco.mjtGeom.mjGEOM_ELLIPSOID,
                size=([.047,.044,.028] if name=="burger" else [.060,.023,.022]),
                mass=(.20 if name=="burger" else .15),rgba=([.68,.35,.11,1] if name=="burger" else [.70,.32,.12,1]),friction=[.8,.01,.001])
    camera_pos=[0,-1.30,1.75]
    wb.add_camera(name="high",pos=camera_pos,quat=_look_at(camera_pos,np.array([0,0,.82])),fovy=60)
    wb.add_light(name="key_light",pos=[0,-.6,2.4],dir=[0,.3,-1],diffuse=[.9,.9,.9],ambient=[.35,.35,.35])

def derive_tcp_offset(model,data,arm):
    """Distal working point from actual collision mesh, not a historical comment."""
    rotation=data.xmat[arm.tcp_body].reshape(3,3)
    origin=data.xpos[arm.tcp_body]
    clouds=[]
    for geom in arm.pad_geoms:
        mesh=int(model.geom_dataid[geom]);start=int(model.mesh_vertadr[mesh])
        vertices=model.mesh_vert[start:start+int(model.mesh_vertnum[mesh])]
        world=vertices@data.geom_xmat[geom].reshape(3,3).T+data.geom_xpos[geom]
        clouds.append((world-origin)@rotation)
    distal=min(float(p[:,2].max()) for p in clouds)
    region=np.concatenate([p[p[:,2]>=distal-.03] for p in clouds])
    center_y=float((region[:,1].min()+region[:,1].max())/2)
    return (0.,center_y,distal-.020)

def build_scene(*, seed=0, include_kitchen=True, layout_id="canonical"):
    layout_sha256 = layout_manifest_sha256(layout_id)
    if not include_kitchen and layout_id != "canonical":
        raise ValueError("A non-canonical layout requires the kitchen scene")
    source=mujoco.MjSpec.from_file(str(MJCF))
    spec=mujoco.MjSpec()
    spec.compiler.degree=False # all generated hinge ranges are radians, same as source FR3
    for field in ("timestep","integrator","impratio","cone","noslip_iterations","enableflags"):
        setattr(spec.option,field,getattr(source.option,field))
    spec.nkey=source.nkey
    spec.modelname="dual_fr3_kitchen"
    # Camera image size is explicit and independent of viewport size.
    spec.visual.global_.offwidth=256
    spec.visual.global_.offheight=256
    for side,x in (("left",-.23),("right",.23)):
        child=source.copy()
        grip=child.body("umi_umi_gripper_base")
        grip.add_camera(name="wrist",pos=[0,-.06,.03],quat=[0,1,0,0],fovy=75)
        frame=spec.worldbody.add_frame(name=side+"_base_frame",pos=[x,.10,TABLE_Z],
            quat=[math.sqrt(.5),0,0,-math.sqrt(.5)])
        spec.attach(child,frame=frame,prefix=side+"_")
    if include_kitchen:
        layout = layout_manifest(layout_id)
        _kitchen(spec, np.random.default_rng(seed), include_rice=layout.get("profile") != "kitchen_platform_v3")
        if "wheelchair_details_v1" in layout.get("visual_features", []):
            _wheelchair_visual_details(spec)
        apply_layout(spec, layout_id)
        from .contact_v4 import apply_contact_v4
        apply_contact_v4(spec, layout)
    model=spec.compile(); data=mujoco.MjData(model)
    arms={s:ArmHandle.resolve(model,s) for s in ARMS}
    for arm in arms.values():
        model.geom_friction[list(arm.pad_geoms),0]=.9
        data.qpos[list(arm.qpos)]=HOME
        data.ctrl[list(arm.actuators)]=HOME
        model.actuator_forcelimited[arm.gripper_actuator]=True
        model.actuator_forcerange[arm.gripper_actuator]=[-40,40]
    if include_kitchen and layout.get("profile") == "kitchen_platform_v3":
        for side, arm in arms.items():
            home = layout["base_poses"][side]["home_q"]
            data.qpos[list(arm.qpos)] = home
            data.ctrl[list(arm.actuators)] = home
    mujoco.mj_forward(model,data)
    arms={side:replace(arm,tcp_offset=derive_tcp_offset(model,data,arm)) for side,arm in arms.items()}
    objects={name:int(mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,name))
        for name in ("apple","pear","banana","tomato","burger","hotdog","rice_container","burger_container","hotdog_container","wood_tray")
        if mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,name)>=0}
    assert not set(arms["left"].qpos)&set(arms["right"].qpos)
    assert not set(arms["left"].actuators)&set(arms["right"].actuators)
    source_sha256=hashlib.sha256(MJCF.read_bytes()).hexdigest()
    builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    compiled_sha256=compiled_model_sha256(model)
    scene_sha256=scene_identity_sha256(source_sha256=source_sha256,layout_id=layout_id,
        layout_sha256=layout_sha256,seed=seed,compiled_sha256=compiled_sha256)
    return KitchenModel(model,data,arms,objects,spec,source_sha256,seed,layout_id,
        layout_sha256,compiled_sha256,scene_sha256,builder_sha256)
