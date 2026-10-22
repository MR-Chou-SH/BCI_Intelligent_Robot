"""Semantic scene IDs, local operating volumes and measured geometry queries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import mujoco
import numpy as np

from .contact import object_geom_ids
from .layouts import layout_manifest
from .limits import joint_control_limits

API_VERSION = "1.2.1"
FRUITS = ("apple", "pear", "banana", "tomato")
MEALS = ("burger", "hotdog")
REMOVED_V3 = ("rice", "rice_container")


def control_identity():
    from .control import DualArmController
    root=Path(__file__).parent
    config={"version":"kitchen-control-v3.3-contact3-research1","control_hz":25,"physics_hz":500,
            "joint_velocity_rad_s":DualArmController.velocity_limit,
            "tcp_command_velocity_m_s":DualArmController.tcp_command_velocity,
            "tcp_measured_velocity_m_s":DualArmController.tcp_velocity_limit,
            "ordinary_contact_limit_n":5.,"intentional_pad_contact_limit_n":40.,
            "tendon_change_m_per_frame":.002,"ordinary_clearance_m":.002,
            "sources":{name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                       for name in ("control.py","safety.py","limits.py","collision.py","devices.py","platform.py","registry.py","contact_execution.py","mechanics.py","research_initialization.py")}}
    return {"control_hash":digest(config),"configuration":config}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def geometry_bounds(model, data, geom, frame_origin=None, frame_rotation=None):
    origin = np.zeros(3) if frame_origin is None else np.asarray(frame_origin)
    frame = np.eye(3) if frame_rotation is None else np.asarray(frame_rotation)
    center = frame.T @ (data.geom_xpos[geom] - origin)
    rotation = frame.T @ data.geom_xmat[geom].reshape(3, 3)
    size = model.geom_size[geom]
    kind = int(model.geom_type[geom])
    if kind == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = int(model.geom_dataid[geom])
        start, count = int(model.mesh_vertadr[mesh]), int(model.mesh_vertnum[mesh])
        points = model.mesh_vert[start:start+count] @ rotation.T + center
        return points.min(axis=0), points.max(axis=0)
    if kind == mujoco.mjtGeom.mjGEOM_BOX:
        extent = np.abs(rotation) @ size
    elif kind == mujoco.mjtGeom.mjGEOM_ELLIPSOID:
        extent = np.sqrt(np.sum((rotation * size) ** 2, axis=1))
    elif kind in (mujoco.mjtGeom.mjGEOM_CAPSULE, mujoco.mjtGeom.mjGEOM_CYLINDER):
        axis = rotation[:, 2]
        extent = np.abs(axis) * size[1]
        extent += size[0] if kind == mujoco.mjtGeom.mjGEOM_CAPSULE else size[0]*np.sqrt(np.maximum(0, 1-axis**2))
    elif kind == mujoco.mjtGeom.mjGEOM_SPHERE:
        extent = np.full(3, size[0])
    else:
        raise ValueError("Unsupported bounded geometry: " + model.geom(geom).name)
    return center-extent, center+extent


def object_bounds(scene, name, *, frame_body=None, data=None):
    m, d = scene.model, data or scene.data
    if name not in scene.objects:
        raise ValueError("Not a movable object: " + name)
    if frame_body is None:
        origin, rotation = np.zeros(3), np.eye(3)
    else:
        body = m.body(frame_body).id
        origin, rotation = d.xpos[body], d.xmat[body].reshape(3, 3)
    boxes = [geometry_bounds(m, d, g, origin, rotation) for g in object_geom_ids(scene, name)]
    return np.min([a for a, _ in boxes], axis=0), np.max([b for _, b in boxes], axis=0)


class SceneRegistry:
    def __init__(self, scene):
        self.scene = scene
        self.layout = layout_manifest(scene.layout_id)
        self.entries = {}
        for name, body in scene.objects.items():
            kind = "fruit" if name in FRUITS else "meal" if name in MEALS else "container" if name.endswith("_container") else "tray"
            self.entries[name] = {"id": name, "type": kind, "body_name": name, "body_id": body,
                "movable": True, "mass_kg": float(scene.model.body_mass[body]),
                "collision_geoms": [scene.model.geom(g).name for g in sorted(object_geom_ids(scene, name))],
                "grasp_frames_status": "CANDIDATE_NOT_PHYSICALLY_VERIFIED"}
        for name, kind, joint in (("sink","sink",None), ("basket","basket",None),
                ("faucet","rotary_valve","faucet_switch"), ("microwave","microwave",None),
                ("microwave_door","hinged_door","microwave_hinge"),
                ("microwave_button","spring_button","microwave_start_slide")):
            body = scene.model.body(name).id
            record = {"id": name, "type": kind, "body_name": name, "body_id": body, "movable": bool(joint)}
            if joint:
                j = scene.model.joint(joint).id
                record.update(joint_name=joint, joint_axis_local=scene.model.jnt_axis[j].tolist(),
                              joint_range=scene.model.jnt_range[j].tolist())
            self.entries[name] = record
        self.entries["user_table"] = {"id":"user_table","type":"delivery_surface","geom_name":"user_table","movable":False}
        self.entries["wheelchair"] = {"id":"wheelchair","type":"wheelchair","movable":False,
                                      "geom_names":[scene.model.geom(g).name for g in range(scene.model.ngeom)
                                                    if scene.model.geom(g).name.startswith(("wheelchair_","wheel_"))]}

    def query(self, ids=None, object_type=None):
        if isinstance(ids, str):
            ids = [ids]
        selected = list(self.entries) if ids is None else list(ids)
        result = []
        m, d = self.scene.model, self.scene.data
        for name in selected:
            if name not in self.entries:
                code = "UNSUPPORTED_OBJECT_IN_SCENE_VERSION" if name in REMOVED_V3 else "UNKNOWN_OBJECT"
                raise ValueError(code + ": " + str(name))
            item = dict(self.entries[name])
            if object_type is not None and item["type"] != object_type:
                continue
            if "body_id" in item:
                b = item["body_id"]
                item["position_world_m"] = d.xpos[b].tolist()
                item["quaternion_wxyz"] = d.xquat[b].tolist()
            elif "geom_name" in item:
                g = m.geom(item["geom_name"]).id
                item["position_world_m"] = d.geom_xpos[g].tolist()
            if name in self.scene.objects:
                lo, hi = object_bounds(self.scene, name)
                item["bounds_world_m"] = [lo.tolist(), hi.tolist()]
            result.append(item)
        return result

    def manifest(self):
        from .mechanics import coupling_manifest
        scene, m, d = self.scene, self.scene.model, self.scene.data
        robots = {}
        for side, arm in scene.arms.items():
            lo, hi = joint_control_limits(m, arm)
            base = m.body(side + "_fr3_link0").id
            robots[side] = {"joint_names":[m.joint(j).name for j in arm.joints],
                "joint_control_limits_rad":[lo.tolist(),hi.tolist()],
                "base_position_world_m":d.xpos[base].tolist(), "base_quaternion_wxyz":d.xquat[base].tolist(),
                "tcp_body_name":m.body(arm.tcp_body).name, "tcp_offset_local_m":list(arm.tcp_offset),
                "flange_body_name":side + "_fr3_link8", "tendon_target_range_m":[0.,.05],
                "gripper_units":"tendon target != mean measured finger slide != physical inner gap"}
        return {"api_version":API_VERSION,"scene_version":self.layout.get("scene_version","legacy"),
            "scene_hash":scene.scene_sha256,"layout_id":scene.layout_id,"layout_hash":scene.layout_sha256,
            "source_model_hash":scene.source_sha256,"compiled_model_hash":scene.compiled_model_sha256,
            "builder_hash":scene.builder_sha256,"layout_approval":self.layout.get("approval","LEGACY"),
            "coordinates":{"world":"RH +X right +Y away +Z up","units":"m rad s N Nm","quaternion":"wxyz",
                           "unity_basis":"position=(x,z,y), rotation=C R C^T, scale=1"},
            "physics_authority":"MuJoCo","physics_dt_s":float(m.opt.timestep),"control_hz":25,
            "control_identity":control_identity(),"robots":robots,"objects":self.query(),"object_ids":list(self.entries),
            "task_catalog_compatibility":{"ids_preserved":"L01-L06 R01-R09 B01 C01-C12",
                "supported_meal_bindings":list(MEALS),"removed_object_bindings":list(REMOVED_V3),
                "automatic_task_strategy":"EXTERNAL_PLANNER_RESPONSIBILITY"},
            "approximations":self.layout.get("physical_approximations",[]),
            "internal_mechanical_constraints":coupling_manifest(scene),
            "capability_evidence":"Per-object physical success requires separate evidence; manifest presence is not PASS"}
