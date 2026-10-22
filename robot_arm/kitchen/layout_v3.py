"""Apply the preserved V3 candidate transforms without changing robot internals."""
from __future__ import annotations

import math
import mujoco
import numpy as np


def _box(parent, name, half, position, color):
    return parent.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX,
                          size=half, pos=position, rgba=color,
                          friction=[.8, .01, .001], solref=[.02, 1])


def _look_at(position, target):
    z = np.asarray(position, dtype=float) - np.asarray(target, dtype=float)
    z /= np.linalg.norm(z)
    x = np.cross([0., 0., 1.], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, np.column_stack((x, y, z)).ravel())
    return q


def apply_v3_layout(spec, layout):
    for side, mount in layout["base_poses"].items():
        yaw = mount["yaw_rad"]
        frame = spec.worldbody.add_frame(name=side + "_v3_mount",
            pos=mount["position"], quat=[math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)])
        # The original attachment has an intermediate frame. Bind the actual root.
        spec.body(side + "_base").set_frame(frame)
    for name, position in layout["body_positions"].items():
        spec.body(name).pos = position
    yaw = layout["microwave_yaw_rad"]
    spec.body("microwave").quat = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
    if layout["door_variant"] == "mirrored_left_hinge":
        for name in ("microwave_door", "microwave_button"):
            p = np.asarray(spec.body(name).pos).copy()
            p[1] *= -1
            spec.body(name).pos = p
        for name in ("microwave_door_panel", "microwave_door_handle"):
            p = np.asarray(spec.geom(name).pos).copy()
            p[1] *= -1
            spec.geom(name).pos = p
        spec.joint("microwave_hinge").axis = [0, 0, -1]
    elif layout["door_variant"] != "original_right_hinge":
        raise ValueError("Unknown physical microwave door variant")
    for name in ("counter_left", "counter_right", "counter_front", "counter_back"):
        spec.delete(spec.geom(name))
    x0, x1, y0, y1 = layout["counter_bounds_xy"]
    sx, sy, _ = layout["body_positions"]["sink"]
    for label, a, b, c, d in (
        ("left", x0, sx - .188, y0, y1),
        ("right", sx + .188, x1, y0, y1),
        ("front", sx - .188, sx + .188, y0, sy - .178),
        ("back", sx - .188, sx + .188, sy + .178, y1)):
        if a >= b or c >= d:
            raise ValueError("Invalid supported worktop/sink opening")
        _box(spec.worldbody, "v3_counter_" + label,
             [(b-a)/2, (d-c)/2, .025], [(a+b)/2, (c+d)/2, .775], [.77,.80,.79,1])
    for ix, x in enumerate((-.78, .78)):
        for iy, y in enumerate((-.37, .37)):
            spec.geom(f"counter_leg_{x}_{y}").pos = [
                x0+.06 if ix == 0 else x1-.06, y0+.06 if iy == 0 else y1-.06, .375]
    spec.geom("user_table").pos = layout["delivery_table_center"]
    spec.geom("user_table").size = layout["delivery_table_half_size"]
    for geom in spec.geoms:
        if geom.name.startswith(("wheelchair_", "wheel_")):
            endpoints = np.asarray(geom.fromto).copy()
            if np.isfinite(endpoints).all():
                endpoints[[1, 4]] -= .14
                geom.fromto = endpoints
            else:
                p = np.asarray(geom.pos).copy()
                p[1] -= .14
                geom.pos = p
    for x in (-.31, .31):
        _box(spec.worldbody, f"v3_delivery_support_{x}", [.025,.025,.365],
             [x,-.85,.365], [.3,.32,.34,1])
    spec.visual.global_.offwidth = 1600
    spec.visual.global_.offheight = 1100
    for name, position, target in (
        ("v3_top", [0.,-.12,3.9], [0.,-.12,.8]),
        ("v3_oblique", [2.4,-3.,2.65], [0.,-.02,.85]),
        ("v3_front", [0.,-3.,1.9], [0.,0.,.9]),
        ("v3_faucet", [-1.28,-.50,1.35], [-.82,.14,.90]),
        ("v3_microwave", [.10,-.65,1.55], [.87,.25,.97])):
        quat = [1.,0.,0.,0.] if name == "v3_top" else _look_at(position, target)
        spec.worldbody.add_camera(name=name, pos=position, quat=quat, fovy=50)
