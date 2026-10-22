"""Declared rigid support/handle proxies for the resumed V3 candidate only."""
from __future__ import annotations

import math
import mujoco


def _hull(spec, body_name, geom_name, rings, mass, color):
    vertices = []
    for height, rx, ry in rings:
        for index in range(32):
            angle = 2*math.pi*index/32
            vertices.extend((rx*math.cos(angle), ry*math.sin(angle), height))
    mesh_name = body_name+"_flat_support_hull_v4"
    spec.add_mesh(name=mesh_name, uservert=vertices)
    spec.delete(spec.geom(geom_name))
    spec.body(body_name).add_geom(name=geom_name, type=mujoco.mjtGeom.mjGEOM_MESH,
        meshname=mesh_name, mass=mass, rgba=color, friction=[.8,.01,.001], solref=[.02,1])


def apply_contact_v4(spec, layout):
    from .mechanics import add_jaw_coupling
    add_jaw_coupling(spec, layout)
    if layout.get("contact_model") != "rigid_support_and_standoff_v4":
        return
    if layout.get('apple_primitive') == 'equal_shape_sphere_v1':
        apple=spec.geom('apple_collision')
        if max(abs(float(r)-.035) for r in apple.size)>1e-12:
            raise ValueError('Equal-shape sphere requires the original35mm spherical envelope')
        apple.type=mujoco.mjtGeom.mjGEOM_SPHERE
        apple.size=[.035,0.,0.]
    if layout.get('button_mount') == 'recessed_sidewall_pocket_v1':
        # A6mm front-edge pocket provides travel clearance for the5mm spring key.
        side='front' if spec.body('microwave_button').pos[1]<0 else 'back'
        wall=spec.geom('microwave_side_'+side)
        y=float(wall.pos[1]);color=list(wall.rgba)
        wall.pos=[.003,y,.17];wall.size=[.197,.012,.17]
        for label,z,half_z in (('lower',.071,.071),('upper',.259,.081)):
            spec.body('microwave').add_geom(name='microwave_button_pocket_'+label,
                type=mujoco.mjtGeom.mjGEOM_BOX,pos=[-.197,y,z],size=[.003,.012,half_z],
                rgba=color,friction=[.8,.01,.001],solref=[.02,1])
    # Finite flat bottoms represent a pear calyx and the supporting hotdog bun,
    # rather than an upright prolate ellipsoid and a nearly circular rolling sausage.
    _hull(spec, "pear", "pear_collision", [(-.045,.012,.012),(-.035,.025,.025),
        (-.018,.032,.032),(.005,.027,.027),(.028,.015,.015),(.045,.003,.003)],
        .18, [.75,.75,.13,1])
    _hull(spec, "hotdog", "hotdog_food", [(-.022,.047,.014),(-.015,.057,.021),
        (0.,.060,.023),(.014,.054,.021),(.022,.040,.011)], .15, [.70,.32,.12,1])
    if layout.get('burger_support') == 'flat_bun_v1':
        _hull(spec, 'burger', 'burger_food', [(-.028,.032,.030),(-.020,.044,.041),
            (0.,.047,.044),(.018,.041,.038),(.028,.025,.024)], .20, [.68,.35,.11,1])
    # A real stand-off handle leaves 43 mm behind the bar, not the old 2 mm slit.
    handle = spec.geom("microwave_door_handle")
    handle.pos = [-.060, -.25 if layout["door_variant"] == "mirrored_left_hinge" else .25, .145]
    handle.size = [.009,.010,.060]
    door = spec.body("microwave_door")
    for index, z in enumerate((.085,.205)):
        door.add_geom(name="microwave_handle_post_"+str(index), type=mujoco.mjtGeom.mjGEOM_CAPSULE,
            fromto=[-.010,handle.pos[1],z,-.060,handle.pos[1],z], size=[.006],
            mass=.012, rgba=[.55,.56,.57,1], friction=[.8,.01,.001], solref=[.02,1])
    if layout.get("meal_carrier") in ("raised_burger_carry_bar_v1", "raised_burger_carry_bar_v2", "raised_burger_carry_bar_v3"):
        carrier = spec.body("burger_container")
        for side, x in (("left", -.073), ("right", .073)):
            bar = spec.geom("burger_grasp_"+side)
            bar.pos = [x, 0, .055]
            bar.size = [.018, .025, .010]
            bar.mass = .005
            if layout["meal_carrier"].endswith("v3"):
                bar.size = [.025,.025,.010]
                bar.quat = [math.cos(math.pi/12),0.,math.sin(math.pi/12),0.]
            for index, y in enumerate((-.024, .024)):
                foot_x = x if layout["meal_carrier"].endswith("v1") else math.copysign(.063, x)
                carrier.add_geom(name=f"burger_handle_post_{side}_{index}",
                    type=mujoco.mjtGeom.mjGEOM_CAPSULE,
                    fromto=[foot_x,y,.006,x,y,.055], size=[.003], mass=.0025,
                    rgba=[.85,.84,.80,1], friction=[.8,.01,.001], solref=[.02,1])
