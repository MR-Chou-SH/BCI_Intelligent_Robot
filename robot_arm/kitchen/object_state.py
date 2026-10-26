"""Read-only, same-qpos semantic state queries on a detached MjData."""
from dataclasses import replace
import copy
import math

import mujoco
import numpy as np

from .registry import SceneRegistry
from .contact import object_geom_ids

SCHEMA = "kitchen_object_states_v1"


def key_object_states(scene, controller):
    """Small state interface used by recording: pose and task flags only."""
    data=mujoco.MjData(scene.model)
    data.qpos[:]=scene.data.qpos
    mujoco.mj_kinematics(scene.model,data)
    objects=[]
    for name,body in scene.objects.items():
        objects.append({"id":name,"position_world_m":data.xpos[body].tolist(),
                        "quaternion_world_wxyz":data.xquat[body].tolist(),
                        "cleaned":name in controller.devices.cleaned,
                        "heated":name in controller.devices.heated})
    return {"sim_tick":int(controller.tick),"objects":objects,"images_included":False}


def object_states(scene, controller, *, ids=None, object_type=None):
    # mj_step integrates qpos after its last position-stage calculation. Forward
    # a clone so poses, velocities and predicates match qpos without changing the
    # live solver's warmstart, forces, contacts, device timers or physics clock.
    data = mujoco.MjData(scene.model)
    mujoco.mj_copyData(data, scene.model, scene.data)
    mujoco.mj_forward(scene.model, data)
    view = replace(scene, data=data)
    device = copy.copy(controller.devices)
    device.scene = view
    items = SceneRegistry(view).query(ids, object_type)
    for item in items:
        if "body_id" in item:
            velocity = np.zeros(6)
            mujoco.mj_objectVelocity(scene.model, data, mujoco.mjtObj.mjOBJ_BODY,
                                    item["body_id"], velocity, 0)
            item["angular_velocity_world_rad_s"] = velocity[:3].tolist()
            item["linear_velocity_world_m_s"] = velocity[3:].tolist()
        if "joint_name" in item:
            joint = scene.model.joint(item["joint_name"]).id
            item["joint_position"] = float(data.qpos[scene.model.jnt_qposadr[joint]])
            item["joint_velocity"] = float(data.qvel[scene.model.jnt_dofadr[joint]])
            hinge = scene.model.jnt_type[joint] == mujoco.mjtJoint.mjJNT_HINGE
            item["joint_position_unit"] = "rad" if hinge else "m"
            item["joint_velocity_unit"] = "rad/s" if hinge else "m/s"
            if item["id"] == "microwave_door":
                angle = item["joint_position"]
                item["door_state"] = "closed" if angle <= math.radians(5) else "open" if angle >= math.radians(70) else "partly_open"
                item["door_state_thresholds_rad"] = {"closed_max": math.radians(5), "open_min": math.radians(70)}
                item["passage_clearance_verified"] = False
            elif item["id"] == "faucet":
                item["water_on"] = bool(device.snapshot()["water_on"])
            elif item["id"] == "microwave_button":
                item["pressed_depth_met"] = item["joint_position"] >= .003
                item["press_latched"] = bool(device.button_latched)
        if item["id"] in scene.objects:
            name = item["id"]
            item["grasp"] = device.grasp_evidence(name)
            item["in_basket"] = device.in_basket(name) if name in device.clean_seconds else False
            item["in_microwave"] = device.in_microwave(name)
            item["cleaned"] = name in device.cleaned
            item["heated"] = name in device.heated
            own_geoms = object_geom_ids(scene, name)
            contacting = set()
            for contact in data.contact:
                g1, g2 = int(contact.geom1), int(contact.geom2)
                other = g2 if g1 in own_geoms else g1 if g2 in own_geoms else None
                if (other is not None and other not in own_geoms and
                        not scene.model.body(int(scene.model.geom_bodyid[other])).name.startswith(("left_", "right_"))):
                    contacting.add(other)
            item["support"] = device.support_evidence(name, [scene.model.geom(g).name for g in sorted(contacting)])
            item["supporting_geom_names"] = sorted({row["support_geom"] for row in item["support"]["contacts"]
                                                   if row["qualifies_as_upward_support"]})
            if item["type"] == "meal":
                carrier = name + "_container"
                item["carrier_id"] = carrier
                item["meal_group_in_microwave"] = device.meal_ready(carrier)
                item["meal_group_heated"] = carrier in device.heated
                item["group_state_semantics"] = "food plus its own carrier; heated is a simulated timer marker"
            elif item["type"] == "container":
                food = name.removesuffix("_container")
                item["payload_ids"] = [food] if food in scene.objects else []
                # Derived from compiled geometry, so edits to a bar's tilt or
                # randomized carrier pose are reflected by the same-tick query.
                item["handle_frames"] = [{
                    "geom_name": scene.model.geom(g).name,
                    "position_world_m": data.geom_xpos[g].tolist(),
                    "rotation_world": data.geom_xmat[g].reshape(3, 3).tolist(),
                    "half_size_m": scene.model.geom_size[g].tolist()}
                    for g in sorted(own_geoms)
                    if scene.model.geom(g).name.startswith(food + "_grasp_")]
    return {"schema": SCHEMA, "sim_tick": int(controller.tick), "sim_time_s": float(data.time),
            "pose_semantics": "world body origin, quaternion wxyz; derived from same-tick qpos in scratch",
            "predicate_semantics": "instantaneous measured evidence, not sustained task success",
            "objects": items, "devices": controller.devices.snapshot(),
            "device_events": copy.deepcopy(controller.devices.events), "images_included": False}
